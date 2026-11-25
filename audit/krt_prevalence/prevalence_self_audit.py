"""Prevalence self-audit: raw sql approximation against krt_prevalent.

Both cohorts are built for the same prevalence point and compared on ukrdcid.
Only aggregate counts are printed. Patient level rows go to the git-ignored
.do_not_commit folder.

Run from the repo root:
    python audit/krt_prevalence/prevalence_self_audit.py
"""

import datetime as dt
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from ukrdc_stats.cohorts.base import krt_prevalent
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.utils.data import main_centres
from ukrdc_stats.utils.database import get_sessionmaker, load_ukrdc_url_from_config

SERVER = "ukrdc_live"
TEST_CENTRES = [
    "RAQ01",  # Lister (Stevenage)
    "RH8",  # RD&E
    "RHW01",  # Reading
    "RCSLB",
]
PREVALENCE_POINT = dt.datetime.combine(dt.date.today(), dt.time.min)
SQL_PATH = (
    Path(__file__).resolve().parents[1] / "sql_raw" / "approximation_functions.sql"
)
OUTPUT_DIR = Path(".do_not_commit")

FLAGS = ["in_python", "in_sql", "centre_match", "modality_match"]
MISMATCH_COLUMNS = [
    "ukrdcid",
    "pid_py",
    "pid_sql",
    "centre_code",
    "sendingfacility",
    "modality_py",
    "modality_sql",
    *FLAGS,
    "match_category",
]
MATCH_CATEGORIES = [
    "not_in_sql",
    "not_in_python",
    "centre_mismatch",
    "modality_mismatch",
    "full_match",
]
MISSING = "NONE"

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH") or ""


def _function_definitions() -> str:
    """Read the sql file without BEGIN/COMMIT, which SQLAlchemy already manages."""
    lines = SQL_PATH.read_text().splitlines()
    return "\n".join(
        line for line in lines if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
    )


def load_sql_prevalent(
    centres: list[str], prevalence_point: dt.datetime
) -> pd.DataFrame:
    """Run pg_temp.prevalent_cohort for each centre on one connection."""
    engine = create_engine(load_ukrdc_url_from_config(SERVER, KEYPATH))
    query = text(
        "SELECT ukrdcid, pid, sendingfacility, registry_code_type "
        "FROM pg_temp.prevalent_cohort(ARRAY[:centre], CAST(:point AS date))"
    )
    frames = []
    with engine.connect() as connection:
        connection.exec_driver_sql(_function_definitions())
        for centre in centres:
            params = {"centre": centre, "point": prevalence_point.date()}
            rows = connection.execute(query, params).all()
            print(f"  sql  {centre:<6} {len(rows):>6} rows")
            frames.append(
                pd.DataFrame(
                    rows,
                    columns=["ukrdcid", "pid", "sendingfacility", "modality"],
                )
            )
    engine.dispose()

    sql = pd.concat(frames, ignore_index=True)
    return sql.drop_duplicates("ukrdcid")


def load_python_prevalent(
    centres: list[str], prevalence_point: dt.datetime
) -> pd.DataFrame:
    """Run krt_prevalent for each centre."""
    frames = []
    with get_sessionmaker(SERVER, keypath=KEYPATH, caching=False)() as session:
        for centre in centres:
            try:
                cohort = krt_prevalent(session, centre, prevalence_point)
            except EmptyCohortError:
                print(f"  py   {centre:<6}      0 rows")
                continue
            print(f"  py   {centre:<6} {len(cohort):>6} rows")
            frames.append(cohort[["ukrdcid", "pid", "centre_code", "dialtplt"]])

    py = pd.concat(frames, ignore_index=True).rename(columns={"dialtplt": "modality"})
    py["modality"] = py["modality"].replace("HHD", "HD")
    return py.drop_duplicates("ukrdcid")


def categorise_match(compared: pd.DataFrame) -> pd.DataFrame:
    """Label each patient with the first failing check, sorted in MATCH_CATEGORIES order."""
    category = pd.Series(MATCH_CATEGORIES[-1], index=compared.index)
    category[~compared["modality_match"]] = MATCH_CATEGORIES[3]
    category[~compared["centre_match"]] = MATCH_CATEGORIES[2]
    category[~compared["in_python"]] = MATCH_CATEGORIES[1]
    category[~compared["in_sql"]] = MATCH_CATEGORIES[0]
    compared["match_category"] = pd.Categorical(
        category, categories=MATCH_CATEGORIES, ordered=True
    )
    return compared.sort_values("match_category", kind="stable")


def compare(py: pd.DataFrame, sql: pd.DataFrame) -> pd.DataFrame:
    """Outer join on ukrdcid and flag presence, centre and modality agreement."""
    compared = py.merge(
        sql, on="ukrdcid", how="outer", suffixes=("_py", "_sql"), indicator=True
    )
    compared["in_python"] = compared["_merge"] != "right_only"
    compared["in_sql"] = compared["_merge"] != "left_only"
    both = compared["in_python"] & compared["in_sql"]
    compared["centre_match"] = both & (
        compared["centre_code"] == compared["sendingfacility"]
    )
    compared["modality_match"] = both & (
        compared["modality_py"] == compared["modality_sql"]
    )
    return categorise_match(compared.drop(columns="_merge"))


def main() -> None:
    if not KEYPATH:
        raise RuntimeError(
            "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
        )

    with get_sessionmaker(SERVER, keypath=KEYPATH, caching=False)() as session:
        centres = main_centres(session, PREVALENCE_POINT)

    print(f"\nPrevalence point: {PREVALENCE_POINT:%Y-%m-%d}")
    print(f"Centres (sql): {', '.join(centres)}")
    print(f"Centres (python): {', '.join(TEST_CENTRES)}")
    print("\nExtracting sql cohort")
    sql = load_sql_prevalent(centres, PREVALENCE_POINT)
    print("\nExtracting python cohort")
    py = load_python_prevalent(TEST_CENTRES, PREVALENCE_POINT)
    print(f"\nSQL patients: {len(sql)}")
    print(f"Python patients: {len(py)}")

    compared = compare(py, sql)
    python_rows = compared.loc[compared["in_python"], FLAGS]
    print(
        pd.DataFrame(
            {
                "patients": python_rows.sum(),
                "percent": (python_rows.mean() * 100).round(1),
            }
        )
    )

    mismatches = compared.loc[
        ~compared[FLAGS].all(axis=1)
        & (compared["match_category"] != MATCH_CATEGORIES[1]),
        MISMATCH_COLUMNS,
    ].fillna({"modality_py": MISSING, "modality_sql": MISSING})
    OUTPUT_DIR.mkdir(exist_ok=True)
    mismatches.to_csv(OUTPUT_DIR / "prevalence_self_audit_mismatches.csv", index=False)
    print(f"\nOutput written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
