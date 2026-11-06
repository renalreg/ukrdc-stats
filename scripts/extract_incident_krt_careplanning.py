import datetime as dt
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import krt_incident
from ukrdc_stats.labellers.demographics import age, sex
from ukrdc_stats.labellers.clinical import pre_start_careplanning
from ukrdc_stats.utils.database import get_sessionmaker
from ukrdc_stats.utils.data import aggregate_data


config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

YEAR_START: int = 2024
QUARTER_START: int = 3
NO_OF_QUARTERS: int = 1
OUTPUT_DIR: Path = Path(".do_not_commit") 
SERVER: str = "ukrdc_live"
OUTPUT_FILE: Path = Path(f"krt_incident_careplanning_{SERVER}_{YEAR_START}.csv")

CENTRES = [
    "RL403",
    "99RQR13",
    "RAE05",
    "RCB55",
    "RF201",
    "RQR13",
]

def main():
    with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
        #region_map = map_codes("RR1+", "URTS_region", session)
        #facility_names = lookup_codes("RR1+", "description", session)
        quarterly_reports = []
        for quarter in range(QUARTER_START - 1, QUARTER_START + NO_OF_QUARTERS - 1):
            # Calculate the start and end times to calculate for
            current_quarter = (quarter % 4) + 1
            current_year = YEAR_START + quarter // 4
            quarter_start = dt.datetime(current_year, current_quarter * 3 - 2, 1)
            if current_quarter < 4:
                quarter_end = dt.datetime(
                        current_year, current_quarter * 3 + 1, 1
                    ) - dt.timedelta(days=1)
            else:
                quarter_end = dt.datetime(current_year, 12, 31)

            krt_incident_reports = []
            for centre in CENTRES:
                # Extract incident and label
                krt_incident_report = krt_incident(session, centre, quarter_end, quarter_start)
                krt_incident_report = age(krt_incident_report, quarter_end, session = session)
                krt_incident_report = sex(krt_incident_report, session = session)
                krt_incident_report = pre_start_careplanning(session, krt_incident_report, "TPLTassess")
                  
                # Recode transplants
                
                krt_incident_reports.append(krt_incident_report)

            krt_incident_combined = pd.concat(krt_incident_reports, ignore_index=True)
            krt_incident_combined["year"] = current_year
            krt_incident_combined["quarter"] = current_quarter
            quarterly_reports.append(krt_incident_combined)

    combined = pd.concat(quarterly_reports)

    # count heads split by careplanning assessment outcome
    output = aggregate_data(
        cohort_wide=combined,
        column_attributes=[
            "centre_code",
            "satellite_code",
            "dialtplt",
            "year",
            "quarter",
            "assessmentoutcome",
        ],
        row_attributes=[
            "age",
            "sex",
        ],
    )

    output.to_csv(OUTPUT_DIR / OUTPUT_FILE, index=False)

    print(output.head())


if __name__ == "__main__":
    main()