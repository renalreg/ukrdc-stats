"""
Example of custom cohort which extends the base cohorts
"""


import pandas as pd
import datetime as dt
from pathlib import Path
from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import krt_prevalent
from ukrdc_stats.labellers.geography import adult_paed
from ukrdc_stats.labellers.clinical import vascular_access, hd_dialysis_frequency
from ukrdc_stats.utils.database import get_sessionmaker
from ukrdc_stats.utils.data import aggregate_data


config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

YEAR_START: int = 2022
QUARTER_START: int = 1
NO_OF_QUARTERS: int = 12
OUTPUT_DIR: Path = Path(".do_not_commit") 
SERVER: str = "ukrdc_live"
OUTPUT_FILE: Path = Path(f"dialysis_one_year_{SERVER}_{YEAR_START}.csv")

CENTRES = [
        # live
    "RAJ",   # MSE
    "RAQ01", # Lister
    "RCSLB", # Nottingham
    "RH8",   # RD&E
    "RHW01", # Reading
    "RK7CC", # Sheffield
    "RL403", # Wolverhampton
    "RNJ00", # Barts
    "RFPFG", # Derby
    "RBD01", # Dorset
    "RLZ01", # Shrewsbury
    "RP5",   # Doncaster
    "99RQR13",
    "RAE05",
    "RCB55",
    "RF201",
    "RQR13",
]

def hd_one_year(
    session,
    centre: str,
    prevalence_point: dt.datetime,
    time_on_dialysis: dt.timedelta = dt.timedelta(days=365),
) -> pd.DataFrame:
    """
    Cohort of prevalent incident HD patients who have been on thier current
    dialysis modality for at least a year. In it's current iteration it might
    be relatively sensitive to how treatments are coded. This is also an 
    example of how the base cohort generating functions might be extended to 
    produce custom cohorts. 
    """


    base_cohort = krt_prevalent(
        session,
        centre,
        prevalence_point
    )

    # generate a slice of the cohort which focuses on a subset of the ichd patients
    base_cohort["time_since_start"] = prevalence_point - base_cohort["fromtime"]
    base_cohort = base_cohort[
        base_cohort.registry_code_type.isin(["HD"])
        & (base_cohort["time_since_start"] >= time_on_dialysis)
    ].copy()

    return base_cohort


def main():
    with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
        quarterly_reports = []
        for quarter in range(QUARTER_START - 1, QUARTER_START + NO_OF_QUARTERS - 1):
            current_quarter = (quarter % 4) + 1
            current_year = YEAR_START + quarter // 4
            if current_quarter < 4:
                quarter_end = dt.datetime(
                    current_year, current_quarter * 3 + 1, 1
                ) - dt.timedelta(days=1)
                quarter_start = dt.datetime(current_year, current_quarter * 3 - 2, 1)
            else:
                quarter_end = dt.datetime(current_year, 12, 31)
                quarter_start = dt.datetime(current_year, 10, 1)

            for centre in CENTRES:
                report = hd_one_year(session, centre, quarter_end)
                report = adult_paed(session, report)
                report = vascular_access(
                    report, session, mode="prevalent", prevalence_date=quarter_end
                )
                report = hd_dialysis_frequency(
                    session, report, quarter_start, quarter_end
                )
                report["year"] = current_year
                report["quarter"] = current_quarter
                quarterly_reports.append(report)

    combined = pd.concat(quarterly_reports, ignore_index=True)

    combined["dialtplt_dup"] = combined["dialtplt"]
    output = aggregate_data(
        cohort_wide=combined,
        column_attributes=[
            "centre_code",
            "satellite_code",
            "adult_paed",
            "dialtplt_dup",
            "year",
            "quarter",
        ],
        row_attributes=[
            "dialtplt",
            "access",
            "median_sessions_per_week",
        ],
    )
    output.rename(columns={"dialtplt_dup": "dialtplt"}, inplace=True)

    output.to_csv(OUTPUT_DIR / OUTPUT_FILE, index=False)

    print(output.head())

if __name__ == "__main__":
    main()
