"""This is a manual test to check the totals calculated by the calculator 
functions against know totals from UKKA published ckd demographics data.
"""
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.cohorts.base import ckd_prevalent
from ukrdc_stats.labellers.geography import imd, main_satellite_centres
from ukrdc_stats.labellers.clinical import prevalent_careplanning
from ukrdc_stats.utils.data import aggregate_data
from ukrdc_stats.utils.database import get_sessionmaker
import datetime as dt
from dotenv import dotenv_values
from pathlib import Path
import pandas as pd

from ukrdc_stats.utils.data import map_codes


def transform_ref_demog(ref_data: pd.DataFrame) -> pd.DataFrame:
    # ugly hardcoded transformation to match the format expected by the calculator
    females = pd.DataFrame()
    females["centre_code"] = ref_data["centre_code"]
    females["count"] = ref_data["N with CKD"] * (1 - ref_data["% male"] / 100.)
    females["variable"] = "Female"
    females["attribute"] = "sex"


    males = pd.DataFrame()
    males["centre_code"] = ref_data["centre_code"]
    males["count"] = ref_data["N with CKD"] * ref_data["% male"] / 100.
    males["variable"] = "Male"
    males["attribute"] = "sex"
    
    black = pd.DataFrame()
    black["centre_code"] = ref_data["centre_code"]
    black["count"] = ref_data["N with CKD"] * ref_data["% Black"] / 100.
    black["variable"] = "Black"
    black["attribute"] = "ukkaethnicity"
    
    asian = pd.DataFrame()
    asian["centre_code"] = ref_data["centre_code"]
    asian["count"] = ref_data["N with CKD"] * ref_data["% Asian"] / 100.
    asian["variable"] = "Asian"
    asian["attribute"] = "ukkaethnicity"

    
    other = pd.DataFrame()
    other["centre_code"] = ref_data["centre_code"]
    other["count"] = ref_data["N with CKD"] * ref_data["% Other"] / 100.
    other["variable"] = "Other"
    other["attribute"] = "ukkaethnicity"
    
    white = pd.DataFrame()
    white["centre_code"] = ref_data["centre_code"]
    white["count"] = ref_data["N with CKD"] * ref_data["% White"] / 100.
    white["variable"] = "White"
    white["attribute"] = "ukkaethnicity"

    ref_demog = pd.concat(
        [
            males,
            females,
            black,
            asian,
            other,
            white,
        ],
        axis=0,
    )

    return ref_demog


SERVER = "ukrdc_live"
YEAR = 2023

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

REF_DATA_PATH = Path("tests/test_statistical_accuracy/data/demog_2023.xlsx")


if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )
prevalence_date = dt.datetime(2023, 12, 31)

with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
    stats_codes = {v: k for k, v in map_codes("RR1+", "STATISTICIAN_CODE", session).items()}
    ref_data = pd.read_excel(REF_DATA_PATH, sheet_name="CKD")
    ref_data["centre_code"] = ref_data["Centre"].map(stats_codes)
    ref_data = transform_ref_demog(ref_data)
    base_cohort = pd.DataFrame()
    for centre in ref_data["centre_code"].unique():
        try:
            centre_cohort = ckd_prevalent(session, centre, prevalence_date)
            base_cohort = pd.concat([base_cohort, centre_cohort])
        
        except (EmptyCohortError, ValueError):
            continue

    base_cohort = main_satellite_centres(session, base_cohort)
    aggregate_data = aggregate_data(base_cohort, ["centre_code"], ["sex", "age", "ukkaethnicity"])
    comparison_data = aggregate_data.merge(ref_data, on=["centre_code", "variable", "attribute"], how="left", suffixes=("_ukrdc", "_ref"))
    
    print(comparison_data)
    comparison_data["diff"] = (comparison_data["count_ukrdc"] - comparison_data["count_ref"]) * 100 / comparison_data["count_ref"]
    
    output_dir = Path("tests/test_statistical_accuracy/output")
    output_dir.mkdir(exist_ok=True)
    
    comparison_data.to_csv(output_dir / "ckd_demog_comparison.csv", index=False)
    
    print(f"Output written to {output_dir}")
    print(":)")