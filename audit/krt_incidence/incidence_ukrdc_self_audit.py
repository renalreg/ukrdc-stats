"""Audit(ai slop) the incident cohort against itself using raw sql approximations.

Builds an independent incident cohort with pg_temp.incident_cohort (see
audit/sql_raw/approximation_functions.sql) and compares it, at count and
patient level, with ukrdc_stats.cohorts.base.krt_incident.
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.orm import Session

from ukrdc_stats.cohorts.base import krt_incident
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.utils.database import get_sessionmaker

SERVER = "ukrdc_live"

# hardcoded for testing; treated as "national"
CENTRES = ["RAJ", "RAQ01", "RCSLB", "RH8", "RNJ00"]

END_YEAR = 2026
END_QUARTER = 2
NO_OF_YEARS = 3

OUTPUT_DIR = Path(".do_not_commit")
SQL_PATH = (
    Path(__file__).resolve().parents[1] / "sql_raw" / "approximation_functions.sql"
)

MODALITIES = ["HD", "PD", "TX"]
# modality placeholder for patients found by only one side
MISSING = "NONE"
STATUSES = ["match", "modality_diff", "centre_diff", "python_only", "sql_only"]
PERIOD_KEYS = ["period_type", "period_start"]

PY_COLUMNS = ["ukrdcid", "pid", "centre_code", "dialtplt", "timeline_start"]
SQL_COLUMNS = ["ukrdcid", "pid", "treatment_type_start"]
DISCREPANCY_COLUMNS = [
    "ukrdcid",
    "pid_py",
    "pid_sql",
    "centre_code",
    "side",
    "dialtplt",
    "treatment_type_start",
    "timeline_start",
    "status",
    "period_type",
    "period_start",
    "period_end",
]

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )


@dataclass
class Period:
    """Analysis window; sql treats it as [start, end), python as [start, end]."""

    period_type: str
    start: dt.datetime
    end: dt.datetime


def _window(period_type: str, months: int, steps_back: int) -> Period:
    """Window of `months` length, `steps_back` windows before the audit end."""
    audit_end = pd.Timestamp(END_YEAR, 1, 1) + pd.DateOffset(months=3 * END_QUARTER)
    end = audit_end - pd.DateOffset(months=months * steps_back)
    start = end - pd.DateOffset(months=months)
    return Period(period_type, start.to_pydatetime(), end.to_pydatetime())


def build_periods() -> list[Period]:
    """Quarters and rolling years, all ending at END_QUARTER of END_YEAR."""
    quarters = [_window("quarter", 3, i) for i in range(4 * NO_OF_YEARS)]
    years = [_window("year", 12, i) for i in range(NO_OF_YEARS)]
    return quarters + years


def load_approximation_functions(session: Session) -> None:
    """Create the pg_temp approximation functions on the session's connection.

    pg_temp functions are connection scoped, so they must be created on the
    same connection every later query in this run uses.
    """
    sql = "\n".join(
        line
        for line in SQL_PATH.read_text().splitlines()
        if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
    )
    session.connection().exec_driver_sql(sql)


def sql_cohort(session: Session, period: Period) -> pd.DataFrame:
    """Approximate incident cohort, queried one centre at a time.

    A patient sent by two centres appears under each centre.
    """
    # CAST rather than ::date, which stops text() recognising the bind params
    query = text(
        "SELECT ukrdcid, pid, treatment_type_start FROM pg_temp.incident_cohort("
        "ARRAY[:centre], CAST(:start AS date), CAST(:end AS date))"
    )
    frames = []
    for centre in CENTRES:
        params = {"centre": centre, "start": period.start, "end": period.end}
        rows = session.execute(query, params).all()
        frame = pd.DataFrame(rows, columns=SQL_COLUMNS).assign(centre_code=centre)
        # several pids can share a ukrdcid; keep one row per patient per centre
        frames.append(frame.drop_duplicates(["ukrdcid", "centre_code"]))
    return pd.concat(frames, ignore_index=True)


def python_cohort(session: Session, period: Period) -> pd.DataFrame:
    """Incident cohort from krt_incident for every centre.

    krt_incident keeps timelines starting exactly on end_date, so the end is
    nudged back 1 microsecond to match the sql [start, end) window.
    """
    end = period.end - dt.timedelta(microseconds=1)
    frames = []
    for centre in CENTRES:
        try:
            cohort = krt_incident(
                session, centre, start_date=period.start, end_date=end
            )
        except EmptyCohortError:
            continue
        frames.append(cohort[PY_COLUMNS])
    if not frames:
        return pd.DataFrame(columns=PY_COLUMNS)
    py = pd.concat(frames, ignore_index=True)
    return py.assign(dialtplt=py["dialtplt"].replace("HHD", "HD"))


def compare(py: pd.DataFrame, sql: pd.DataFrame, period: Period) -> pd.DataFrame:
    """Outer join the cohorts on ukrdcid + centre and label every row.

    A patient missing from one side is a centre_diff, not python_only or
    sql_only, when the other side has them under a different centre.
    """
    if py.duplicated(["ukrdcid", "centre_code"]).any():
        raise ValueError("krt_incident returned a patient twice for one centre")

    merged = py.merge(
        sql,
        on=["ukrdcid", "centre_code"],
        how="outer",
        indicator=True,
        suffixes=("_py", "_sql"),
    )
    in_py = merged["_merge"] != "right_only"
    in_sql = merged["_merge"] != "left_only"
    py_modality = merged["dialtplt"].fillna(MISSING)
    sql_modality = merged["treatment_type_start"].fillna(MISSING)
    on_other_side = np.where(
        in_py,
        merged["ukrdcid"].isin(sql["ukrdcid"]),
        merged["ukrdcid"].isin(py["ukrdcid"]),
    )

    # conditions are checked in order, first true wins
    status = np.select(
        [
            in_py & in_sql & (py_modality == sql_modality),
            in_py & in_sql,
            on_other_side,
            in_py,
        ],
        ["match", "modality_diff", "centre_diff", "python_only"],
        default="sql_only",
    )
    side = np.where(status == "centre_diff", np.where(in_py, "python", "sql"), "")

    return merged.drop(columns="_merge").assign(
        dialtplt=py_modality,
        treatment_type_start=sql_modality,
        in_py=in_py,
        in_sql=in_sql,
        status=status,
        side=side,
        period_type=period.period_type,
        period_start=period.start,
        period_end=period.end,
    )


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    """Divide, giving NaN rather than inf or 0 where the denominator is zero."""
    return num / den.where(den > 0)


def _side_count(
    compared: pd.DataFrame, keys: list[str], flag: str, modality: str
) -> pd.Series:
    """Patients on one side, counted per keys and that side's modality."""
    present = compared[compared[flag]]
    counts = present.groupby([*keys, modality]).size()
    return counts.rename_axis([*keys, "modality"])


def count_table(compared: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """n_py and n_sql per period (and centre) and modality."""
    keys = [*PERIOD_KEYS, *by]
    counts = (
        pd.concat(
            {
                "n_py": _side_count(compared, keys, "in_py", "dialtplt"),
                "n_sql": _side_count(compared, keys, "in_sql", "treatment_type_start"),
            },
            axis=1,
        )
        .fillna(0)
        .astype(int)
        .reset_index()
    )
    counts["diff"] = counts["n_py"] - counts["n_sql"]
    counts["pct_diff"] = 100 * _ratio(counts["diff"], counts["n_sql"])
    return counts


def metrics(compared: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Patient level agreement per period (and centre).

    SQL is the reference throughout because python is the code under test.
    """
    keys = [*PERIOD_KEYS, *by]
    # one 0/1 column per status, so a groupby sum counts each status
    status_flags = pd.get_dummies(compared["status"]).reindex(
        columns=STATUSES, fill_value=False
    )
    stats = (
        pd.concat([compared[[*keys, "in_py", "in_sql"]], status_flags], axis=1)
        .groupby(keys)
        .sum()
        .rename(columns={"in_py": "n_py", "in_sql": "n_sql"})
        .reset_index()
    )
    union = stats[STATUSES].sum(axis=1)
    both = stats["match"] + stats["modality_diff"]

    stats["diff"] = stats["n_py"] - stats["n_sql"]
    stats["pct_diff"] = 100 * _ratio(stats["diff"], stats["n_sql"])
    stats["overlap"] = _ratio(both, union)
    stats["python_only_rate"] = _ratio(stats["python_only"], stats["n_py"])
    stats["sql_only_rate"] = _ratio(stats["sql_only"], stats["n_sql"])
    stats["centre_diff_rate"] = _ratio(stats["centre_diff"], union)
    stats["modality_agreement"] = _ratio(stats["match"], both)
    stats["discrepancy_rate"] = 1 - _ratio(stats["match"], union)
    return stats


def modality_accuracy(quarterly: pd.DataFrame) -> pd.DataFrame:
    """Sensitivity and ppv per modality, pooled over quarters and centres."""
    is_match = quarterly["status"] == "match"
    accuracy = pd.DataFrame(
        [
            {
                "modality": modality,
                "n_sql": (quarterly["treatment_type_start"] == modality).sum(),
                "n_py": (quarterly["dialtplt"] == modality).sum(),
                "match": (is_match & (quarterly["dialtplt"] == modality)).sum(),
            }
            for modality in MODALITIES
        ]
    )
    accuracy["sensitivity"] = _ratio(accuracy["match"], accuracy["n_sql"])
    accuracy["ppv"] = _ratio(accuracy["match"], accuracy["n_py"])
    accuracy["min_sensitivity_ppv"] = accuracy[["sensitivity", "ppv"]].min(axis=1)
    return accuracy


def confusion_matrix(quarterly: pd.DataFrame) -> pd.DataFrame:
    """Python modality (rows) against sql modality (columns) over the quarters.

    The NONE row and column hold patients found by only one side.
    """
    matrix = pd.crosstab(
        quarterly["dialtplt"],
        quarterly["treatment_type_start"],
        margins=True,
        margins_name="Total",
    )
    return matrix.rename_axis(index="python \\ sql", columns=None).reset_index()


def centre_ranking(quarterly_agreement: pd.DataFrame) -> pd.DataFrame:
    """Rank centres by mean quarterly discrepancy rate, worst first."""
    non_match = quarterly_agreement[STATUSES[1:]].sum(axis=1)
    return (
        quarterly_agreement.assign(
            abs_pct_diff=quarterly_agreement["pct_diff"].abs(), non_match=non_match
        )
        .groupby("centre_code")
        .agg(
            mean_discrepancy_rate=("discrepancy_rate", "mean"),
            mean_abs_pct_diff=("abs_pct_diff", "mean"),
            non_match_rows=("non_match", "sum"),
        )
        .sort_values("mean_discrepancy_rate", ascending=False)
        .reset_index()
    )


def window_consistency(compared: pd.DataFrame, periods: list[Period]) -> pd.DataFrame:
    """Compare each year's counts with the sum of its four quarters, per centre.

    A gap means the logic depends on the window, e.g. washout or timelines
    chained across a quarter boundary.
    """
    year_of_quarter = {
        pd.Timestamp(quarter.start): pd.Timestamp(year.start)
        for year in periods
        if year.period_type == "year"
        for quarter in periods
        if quarter.period_type == "quarter" and year.start <= quarter.start < year.end
    }
    totals = (
        compared.groupby([*PERIOD_KEYS, "centre_code"])[["in_py", "in_sql"]]
        .sum()
        .rename(columns={"in_py": "n_py", "in_sql": "n_sql"})
        .reset_index()
    )
    is_year = totals["period_type"] == "year"
    yearly = totals[is_year].drop(columns="period_type")
    summed_quarters = (
        totals[~is_year]
        .assign(period_start=totals["period_start"].map(year_of_quarter))
        .groupby(["period_start", "centre_code"])[["n_py", "n_sql"]]
        .sum()
        .reset_index()
    )
    consistency = yearly.merge(
        summed_quarters,
        on=["period_start", "centre_code"],
        how="outer",
        suffixes=("_year", "_sum_quarters"),
    ).fillna(0)
    consistency["n_py_diff"] = (
        consistency["n_py_year"] - consistency["n_py_sum_quarters"]
    )
    consistency["n_sql_diff"] = (
        consistency["n_sql_year"] - consistency["n_sql_sum_quarters"]
    )
    return consistency


def discrepancy_sheet(
    compared: pd.DataFrame, centre: str | None = None
) -> pd.DataFrame:
    """Patient level rows for every non-match, optionally for one centre."""
    discrepancies = compared[compared["status"] != "match"]
    if centre is not None:
        discrepancies = discrepancies[discrepancies["centre_code"] == centre]
    return discrepancies[DISCREPANCY_COLUMNS].sort_values(["status", "period_start"])


def show(title: str, table: pd.DataFrame) -> None:
    """Print an aggregate table. Never pass patient level rows."""
    print(f"\n{title}:")
    print(table.to_string(index=False))


def main() -> None:
    """Run the self-audit over all periods and write the output workbook."""
    periods = build_periods()

    # the whole run shares one session so the pg_temp functions stay visible
    with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
        load_approximation_functions(session)
        compared = pd.concat(
            [
                compare(python_cohort(session, p), sql_cohort(session, p), p)
                for p in periods
            ],
            ignore_index=True,
        )
    quarterly = compared[compared["period_type"] == "quarter"]

    # Step 1: count comparison, nationally and by centre
    agreement_national = metrics(compared, by=[])
    agreement_centre = metrics(compared, by=["centre_code"])
    quarterly_centre = agreement_centre[agreement_centre["period_type"] == "quarter"]
    show(
        "National totals",
        agreement_national[[*PERIOD_KEYS, "n_py", "n_sql", "diff", "pct_diff"]],
    )

    # Step 2: patient level discrepancies (rows go to file only)
    status_by_centre = quarterly_centre.groupby("centre_code")[STATUSES].sum()
    show("Status counts by centre (quarters)", status_by_centre.reset_index())

    # Step 3: which centres and modalities disagree most
    ranking = centre_ranking(quarterly_centre)
    accuracy = modality_accuracy(quarterly)
    show("Centre ranking (quarters)", ranking)
    show("Modality accuracy (quarters, pooled)", accuracy)

    sheets = {
        "counts_national": count_table(compared, by=[]),
        "counts_centre": count_table(compared, by=["centre_code"]),
        "discrepancies": discrepancy_sheet(compared),
        **{f"discrepancies_{c}": discrepancy_sheet(compared, c) for c in CENTRES},
        "agreement_national": agreement_national,
        "agreement_centre": agreement_centre,
        "modality_accuracy": accuracy,
        "confusion_matrix": confusion_matrix(quarterly),
        "centre_ranking": ranking,
        "window_consistency": window_consistency(compared, periods),
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / f"incidence_self_audit_{END_YEAR}Q{END_QUARTER}.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for name, sheet in sheets.items():
            sheet.to_excel(writer, sheet_name=name, index=False)

    print(f"\nOutput written to {output_path}")


if __name__ == "__main__":
    main()
