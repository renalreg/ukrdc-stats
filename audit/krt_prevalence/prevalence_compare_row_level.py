"""Script to compare patients on row level against data extracted from the ukrr
dataset for prevalence at a specified quarter. Assumes ukrr data in the format:

"rrno", "nhsno", "trt", "centre"

"""

import datetime as dt
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values
from sqlalchemy.orm import Session

from ukrdc_stats.cohorts.base import krt_prevalent
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.labellers.base import nhs_number
from ukrdc_stats.utils.database import get_sessionmaker

LIVE_FACILITIES = [
    "RAQ01",  # Lister (Stevenage)
    "RH8",  # RD&E
    "RHW01",  # Reading
    "RCSLB",
]

SERVER = "ukrdc_live"
PREVAlENCE_QUARTER = 120
PREVALENCE_POINT = dt.datetime(2024, 12, 31)  # TODO:calculate from quarter
OUTPUT_PATH = Path(".do_not_commit/prevalence_compare_row_level.csv")
MISMATCH_OUTPUT_PATH = Path(".do_not_commit/prevalence_mismatches.csv")
MISMATCH_COLUMNS = [
    "pid",
    "ukrdcid",
    "sendingfacility",
    "centre_code",
    "dialtplt",
    "rrno",
    "nhs_no",
    "centre",
    "trt",
    "match_category",
]
MATCH_CATEGORIES = [
    "not_ukrr",
    "ukrr_centre_mismatch",
    "ukrr_modality_mismatch",
    "ukrr_full_match",
]

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")


RR_REFERENCE_PATH = Path(".do_not_commit/prevalent_cohort_2024q4.csv")


def load_rr_reference(path: Path) -> pd.DataFrame:
    """Load the UKRR prevalent reference, keyed on nhs_no."""
    rr = pd.read_csv(
        path, usecols=["rrno", "nhsno", "trt", "centre"], dtype={"nhsno": str}
    )
    rr.loc[rr["trt"] == "Xp", "trt"] = "TX"
    return rr.rename(columns={"nhsno": "nhs_no"}).dropna(subset=["nhs_no"])


def build_ukrdc_prevalent(
    session: Session, facilities: list[str], prevalence_point: dt.datetime
) -> pd.DataFrame:
    """Prevalent KRT cohort across facilities at the prevalence point."""
    cohorts = []
    for facility in facilities:
        try:
            cohorts.append(krt_prevalent(session, facility, prevalence_point))
        except EmptyCohortError:
            continue
    return pd.concat(cohorts, ignore_index=True) if cohorts else pd.DataFrame()


def categorise_match(comparison: pd.DataFrame) -> pd.DataFrame:
    """Label each patient with the first failing check, sorted in MATCH_CATEGORIES order."""
    category = pd.Series(MATCH_CATEGORIES[-1], index=comparison.index)
    category[~comparison["modality_match"]] = MATCH_CATEGORIES[2]
    category[~comparison["centre_match"]] = MATCH_CATEGORIES[1]
    category[~comparison["ukkr_patient"]] = MATCH_CATEGORIES[0]
    comparison["match_category"] = pd.Categorical(
        category, categories=MATCH_CATEGORIES, ordered=True
    )
    return comparison.sort_values("match_category", kind="stable")


def build_patient_comparison(
    ukrdc: pd.DataFrame, rr_reference: pd.DataFrame
) -> pd.DataFrame:
    """One row per UKRDC patient with boolean flags for UKRR matches."""
    comparison = ukrdc.merge(rr_reference, on="nhs_no", how="left", indicator=True)
    comparison["ukkr_patient"] = comparison["_merge"] == "both"
    comparison["centre_match"] = comparison["ukkr_patient"] & (
        comparison["centre_code"] == comparison["centre"]
    )
    comparison["modality_match"] = comparison["ukkr_patient"] & (
        comparison["dialtplt"].replace({"HHD": "HD"}) == comparison["trt"]
    )
    return categorise_match(comparison.drop(columns="_merge"))


def main() -> None:
    if not KEYPATH:
        raise RuntimeError(
            "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
        )

    rr_reference = load_rr_reference(RR_REFERENCE_PATH)

    with get_sessionmaker(SERVER, keypath=KEYPATH, caching=False)() as session:
        ukrdc = build_ukrdc_prevalent(session, LIVE_FACILITIES, PREVALENCE_POINT)[
            [
                "pid",
                "ukrdcid",
                "sendingfacility",
                "centre_code",
                "satellite_code",
                "dialtplt",
            ]
        ]
        ukrdc = nhs_number(ukrdc, session).rename(columns={"nhs_number": "nhs_no"})

    comparison = build_patient_comparison(ukrdc, rr_reference)

    mismatches = comparison.loc[
        comparison["match_category"] != MATCH_CATEGORIES[-1], MISMATCH_COLUMNS
    ]
    mismatches.to_csv(MISMATCH_OUTPUT_PATH, index=False)

    # comparison.to_csv(OUTPUT_PATH, index=False)
    print(f"UKRDC patients: {len(ukrdc)}")
    print(comparison[["ukkr_patient", "centre_match", "modality_match"]].sum())


if __name__ == "__main__":
    main()
