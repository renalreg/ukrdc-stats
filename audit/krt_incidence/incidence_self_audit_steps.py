"""Shared steps for the incidence self-audit.

The audit compares two independently built incident cohorts:

- sql: pg_temp.incident_cohort from audit/sql_raw/approximation_functions.sql,
  run on a plain SQLAlchemy connection and loaded into a DataFrame first.
- python: ukrdc_stats.cohorts.base.krt_incident.

self_audit_quarterly.py and self_audit_yearly.py pick the periods, then call
the steps below in order:

    sql = load_sql_cohort(periods)
    py = load_python_cohort(periods)
    report(compare(py, sql), name)

A period is a (start, end) pair covering [start, end).
"""

import datetime as dt
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from ukrdc_stats.cohorts.base import krt_incident
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.utils.database import get_sessionmaker, load_ukrdc_url_from_config

Period = tuple[dt.datetime, dt.datetime]

SERVER = "ukrdc_live"

# hardcoded for testing; treated as "national"
CENTRES = ["RAJ", "RAQ01", "RCSLB", "RH8", "RNJ00"]

OUTPUT_DIR = Path(".do_not_commit")
SQL_PATH = (
    Path(__file__).resolve().parents[1] / "sql_raw" / "approximation_functions.sql"
)

MODALITIES = ["HD", "PD", "TX"]
STATUSES = ["match", "modality_diff", "centre_diff", "python_only", "sql_only"]

# modality shown for a patient on the side that does not have them
MISSING = "NONE"

# one row per patient per centre per period on each side
KEYS = ["period_start", "centre_code", "ukrdcid"]

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH") or ""

if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )


def _progress(start: dt.datetime, end: dt.datetime, centre: str, n: int) -> None:
    """Print one extraction line. Counts only, no patient identifiers."""
    print(f"  {start:%Y-%m-%d} to {end:%Y-%m-%d}  {centre:<6} {n:>6} rows")


# Step 1: load the sql cohort

def _function_definitions() -> str:
    """Read the sql file, dropping BEGIN/COMMIT.

    SQLAlchemy has already opened a transaction on the connection, so the
    file's own BEGIN/COMMIT would clash with it.
    """
    lines = SQL_PATH.read_text().splitlines()
    return "\n".join(
        line for line in lines if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
    )


def load_sql_cohort(periods: list[Period]) -> pd.DataFrame:
    """Run the raw sql approximation for every period and centre.

    pg_temp functions only exist on the connection that created them, so they
    are created and queried inside the same `with` block.
    """
    engine = create_engine(load_ukrdc_url_from_config(SERVER, KEYPATH))
    # CAST rather than ::date, which stops text() recognising the bind params
    query = text(
        "SELECT ukrdcid, pid, treatment_type_start FROM pg_temp.incident_cohort("
        "ARRAY[:centre], CAST(:start AS date), CAST(:end AS date))"
    )

    print("\nExtracting sql cohort")
    frames = []
    with engine.connect() as connection:
        connection.exec_driver_sql(_function_definitions())
        for start, end in periods:
            for centre in CENTRES:
                params = {"centre": centre, "start": start, "end": end}
                rows = connection.execute(query, params).all()
                _progress(start, end, centre, len(rows))
                frame = pd.DataFrame(rows, columns=["ukrdcid", "pid", "modality"])
                frames.append(frame.assign(period_start=start, centre_code=centre))
    engine.dispose()

    sql = pd.concat(frames, ignore_index=True)
    # several pids can share a ukrdcid; keep one row per patient per centre
    return sql.drop_duplicates(KEYS)


# Step 2: load the python cohort
def load_python_cohort(periods: list[Period]) -> pd.DataFrame:
    """Run krt_incident for every period and centre.

    krt_incident keeps timelines starting exactly on end_date, so the end is
    nudged back 1 microsecond to match the sql [start, end) window.
    """
    print("\nExtracting python cohort")
    frames = []
    with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
        for start, end in periods:
            for centre in CENTRES:
                try:
                    cohort = krt_incident(
                        session,
                        centre,
                        start_date=start,
                        end_date=end - dt.timedelta(microseconds=1),
                    )
                except EmptyCohortError:
                    _progress(start, end, centre, 0)
                    continue
                frame = cohort[
                    ["ukrdcid", "pid", "centre_code", "dialtplt", "timeline_start"]
                ]
                _progress(start, end, centre, len(frame))
                frames.append(frame.assign(period_start=start))

    py = pd.concat(frames, ignore_index=True)
    py = py.rename(columns={"dialtplt": "modality"})
    py["modality"] = py["modality"].replace("HHD", "HD")

    if py.duplicated(KEYS).any():
        raise ValueError("krt_incident returned a patient twice for one centre")
    return py


# Step 3: compare


def compare(py: pd.DataFrame, sql: pd.DataFrame) -> pd.DataFrame:
    """Outer join the two cohorts and label each row with a status.

    Statuses:
        match: on both sides with the same modality.
        modality_diff: on both sides with different modalities.
        centre_diff: on one side only, but the other side has the patient
            under a different centre in the same period.
        python_only: on the python side only.
        sql_only: on the sql side only.
    """
    compared = py.merge(
        sql, on=KEYS, how="outer", suffixes=("_py", "_sql"), indicator="found_in"
    )
    compared["found_in"] = compared["found_in"].astype(str).replace(
        {"left_only": "python", "right_only": "sql"}
    )

    py_patients = set(zip(py["period_start"], py["ukrdcid"]))
    sql_patients = set(zip(sql["period_start"], sql["ukrdcid"]))

    def status(row: pd.Series) -> str:
        if row["found_in"] == "both":
            same = row["modality_py"] == row["modality_sql"]
            return "match" if same else "modality_diff"
        patient = (row["period_start"], row["ukrdcid"])
        if row["found_in"] == "python":
            return "centre_diff" if patient in sql_patients else "python_only"
        return "centre_diff" if patient in py_patients else "sql_only"

    compared["status"] = compared.apply(status, axis=1)
    compared["modality_py"] = compared["modality_py"].fillna(MISSING)
    compared["modality_sql"] = compared["modality_sql"].fillna(MISSING)
    return compared


# Step 4: summarise and report


def percent_diff(n_py: pd.Series, n_sql: pd.Series) -> pd.Series:
    """(python - sql) as a % of sql; blank where sql is zero."""
    return (100 * (n_py - n_sql) / n_sql.where(n_sql > 0)).round(1)


def count_table(compared: pd.DataFrame) -> pd.DataFrame:
    """Patients per period, centre and modality on each side."""
    levels = ["period_start", "centre_code", "modality"]
    on_py = compared[compared["found_in"] != "sql"]
    on_sql = compared[compared["found_in"] != "python"]
    n_py = on_py.groupby(["period_start", "centre_code", "modality_py"]).size()
    n_sql = on_sql.groupby(["period_start", "centre_code", "modality_sql"]).size()

    counts = pd.DataFrame(
        {"n_py": n_py.rename_axis(levels), "n_sql": n_sql.rename_axis(levels)}
    )
    counts = counts.fillna(0).astype(int).reset_index()
    counts["pct_diff"] = percent_diff(counts["n_py"], counts["n_sql"])
    return counts


def national_totals(counts: pd.DataFrame) -> pd.DataFrame:
    """Sum the count table over centres and modalities."""
    totals = counts.groupby("period_start")[["n_py", "n_sql"]].sum().reset_index()
    totals["pct_diff"] = percent_diff(totals["n_py"], totals["n_sql"])
    return totals


def status_table(compared: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Status counts per group, plus the share of rows that are not a match."""
    table = pd.crosstab([compared[column] for column in by], compared["status"])
    table = table.reindex(columns=STATUSES, fill_value=0)
    table["discrepancy_rate"] = (1 - table["match"] / table.sum(axis=1)).round(3)
    table.columns.name = None
    return table.reset_index()


def modality_accuracy(compared: pd.DataFrame) -> pd.DataFrame:
    """Sensitivity and ppv per modality, taking sql as the reference."""
    rows = []
    for modality in MODALITIES:
        n_sql = (compared["modality_sql"] == modality).sum()
        n_py = (compared["modality_py"] == modality).sum()
        matched = (
            (compared["status"] == "match") & (compared["modality_py"] == modality)
        ).sum()
        rows.append(
            {
                "modality": modality,
                "n_sql": n_sql,
                "n_py": n_py,
                "match": matched,
                "sensitivity": round(matched / n_sql, 3) if n_sql else None,
                "ppv": round(matched / n_py, 3) if n_py else None,
            }
        )
    return pd.DataFrame(rows)


def confusion_matrix(compared: pd.DataFrame) -> pd.DataFrame:
    """Python modality (rows) against sql modality (columns).

    The NONE row and column hold patients found by only one side.
    """
    matrix = pd.crosstab(
        compared["modality_py"],
        compared["modality_sql"],
        margins=True,
        margins_name="Total",
    )
    return matrix.rename_axis(index="python \\ sql", columns=None).reset_index()


def discrepancies(compared: pd.DataFrame) -> pd.DataFrame:
    """Patient level rows for every non-match. File output only."""
    columns = [
        *KEYS,
        "pid_py",
        "pid_sql",
        "found_in",
        "status",
        "modality_py",
        "modality_sql",
        "timeline_start",
    ]
    rows = compared.loc[compared["status"] != "match", columns]
    return rows.sort_values(["status", "period_start", "centre_code"])


def show(title: str, table: pd.DataFrame) -> None:
    """Print an aggregate table. Never pass patient level rows."""
    print(f"\n{title}:")
    print(table.to_string(index=False))


def report(compared: pd.DataFrame, name: str) -> None:
    """Print aggregate summaries and write every table to one workbook.

    Patient level rows only go to the workbook, in the git-ignored
    .do_not_commit folder.
    """
    counts = count_table(compared)
    totals = national_totals(counts)
    by_centre = status_table(compared, by=["centre_code"])
    accuracy = modality_accuracy(compared)

    show("National totals", totals)
    show("Status counts by centre", by_centre)
    show("Modality accuracy (sql as reference)", accuracy)

    sheets = {
        "national_totals": totals,
        "counts": counts,
        "status_by_centre": by_centre,
        "status_by_period": status_table(compared, by=["period_start", "centre_code"]),
        "modality_accuracy": accuracy,
        "confusion_matrix": confusion_matrix(compared),
        "discrepancies": discrepancies(compared),
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / f"incidence_self_audit_{name}.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, sheet in sheets.items():
            sheet.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"\nOutput written to {output_path}")
