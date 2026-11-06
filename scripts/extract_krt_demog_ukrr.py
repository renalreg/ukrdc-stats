"""
Simplified version of extract_krt_demog.py. Extracts only the incident KRT
cohort using the UKRR sending extract and labels it with age and adult/paed.
"""

import pandas as pd
import datetime as dt
from pathlib import Path
from dotenv import dotenv_values

from sqlalchemy import select
from ukrdc_sqla.ukrdc import Facility

from ukrdc_stats.cohorts.base import krt_incident
from ukrdc_stats.labellers.demographics import age
from ukrdc_stats.labellers.geography import adult_paed
from ukrdc_stats.utils.database import get_sessionmaker
from ukrdc_stats.utils.data import aggregate_data
from ukrdc_sqla.utils.constants import FacilityType
from ukrdc_stats.exceptions import EmptyCohortError

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

YEAR_START: int = 2020
QUARTER_START: int = 1
NO_OF_QUARTERS: int = 1
OUTPUT_DIR: Path = Path(".do_not_commit")
SERVER: str = "ukrdc_live"
SENDING_EXTRACT: str = "UKRR"
OUTPUT_FILE: Path = Path(f"krt_demog_ukrr_{SERVER}_{YEAR_START}.csv")

def get_renal_centres(session):
    query = select(Facility.code).where(Facility.facilitytype.in_([FacilityType.adult_renal_centre, FacilityType.paediatric_renal_centre])) 
    return [row[0] for row in session.execute(query).all()]


def main():
    with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
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
            for centre in get_renal_centres(session):
                try:
                    # Extract incident and label
                    krt_incident_report = krt_incident(
                        session,
                        centre,
                        quarter_end,
                        quarter_start,
                        sending_extract=SENDING_EXTRACT,
                    )
                except EmptyCohortError:
                    print(f"Empty cohort for centre: {centre} Q{quarter}")
                    continue

                krt_incident_report = age(krt_incident_report, quarter_end)
                krt_incident_report = adult_paed(session, krt_incident_report)
                krt_incident_reports.append(krt_incident_report)

            krt_incident_combined = pd.concat(krt_incident_reports, ignore_index=True) if krt_incident_reports else pd.DataFrame()

            # add year and quarter
            krt_incident_combined["year"] = current_year
            krt_incident_combined["quarter"] = current_quarter

            quarterly_reports.append(krt_incident_combined)

    combined = pd.concat(quarterly_reports)

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
            "age",
            "dialtplt",
        ],
    )
    output.rename(columns={"dialtplt_dup": "dialtplt"}, inplace=True)

    # save to csv
    output.to_csv(OUTPUT_DIR / OUTPUT_FILE, index=False)

    print(output.head())


if __name__ == "__main__":
    main()
