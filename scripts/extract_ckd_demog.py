"""
Script to generate cohort for the ukrdc tableau dashboards.
"""
import pandas as pd
import datetime as dt
from pathlib import Path
from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import ckd_prevalent
from ukrdc_stats.labellers.geography import imd
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.utils.database import get_sessionmaker
from ukrdc_stats.utils.data import aggregate_data
from ukrdc_stats.labellers.demographics import ethnicity
from ukrdc_stats.labellers.geography import imd, adult_paed

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

YEAR_START: int = 2024
QUARTER_START: int = 1
NO_OF_QUARTERS: int = 1
OUTPUT_DIR: Path = Path(".do_not_commit")
SERVER: str = "ukrdc_live"
OUTPUT_FILE: Path = Path(f"ckd_demog_{SERVER}_{YEAR_START}.csv")


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

def main():
    with get_sessionmaker(SERVER, keypath=KEYPATH, caching = True)() as session:
        quarterly_reports = []
        for quarter in range(QUARTER_START - 1, QUARTER_START + NO_OF_QUARTERS - 1):
            # Calculate the start and end times to calculate for
            current_quarter = (quarter % 4) + 1
            current_year = YEAR_START + quarter // 4
            if current_quarter < 4:
                quarter_end = dt.datetime(
                        current_year, current_quarter * 3 + 1, 1
                    ) - dt.timedelta(days=1)
            else:
                quarter_end = dt.datetime(current_year, 12, 31)

            ckd_prevalent_reports = []
            for centre in CENTRES:
                # Extract prevalent and label
                try:
                    ckd_prevalent_report = ckd_prevalent(session, centre, quarter_end)
                except EmptyCohortError:
                    print(f"No patients found for centre {centre} in quarter {current_quarter} {current_year}")
                    continue

                ckd_prevalent_reports.append(ckd_prevalent_report)

            ckd_prevalent_combined = pd.concat(ckd_prevalent_reports, ignore_index=True) if ckd_prevalent_reports else pd.DataFrame()

            # add year and quarter
            if not ckd_prevalent_combined.empty:
                combined = ethnicity(ckd_prevalent_combined, session)
                combined = adult_paed(session, ckd_prevalent_combined)
                combined = imd(session, ckd_prevalent_combined)
                combined["year"] = current_year
                combined["quarter"] = current_quarter
            else:
                combined = pd.DataFrame()
            quarterly_reports.append(combined)

    combined = pd.concat(quarterly_reports)

    output = aggregate_data(
        cohort_wide = combined,
        column_attributes = [
            "centre_code",
            "satellite_code",
            "adult_paed",
            "clinictype",
            "year",
            "quarter"
        ],
        row_attributes = [
            "age",
            "imddecile",
            "ethnicity",
            "sex"
        ]
    )

    # save to csv
    output.to_csv(OUTPUT_DIR / OUTPUT_FILE, index=False)

    print(output.head())


if __name__ == "__main__":
    main()