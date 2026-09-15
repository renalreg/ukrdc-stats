import pandas as pd
import datetime as dt
from pathlib import Path
from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import krt_incident, krt_prevalent
from ukrdc_stats.labellers.demographics import age, sex, ethnicity
from ukrdc_stats.labellers.geography import imd, adult_paed
from ukrdc_stats.utils.database import get_sessionmaker
from ukrdc_stats.utils.data import aggregate_data


config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

YEAR_START: int = 2026
QUARTER_START: int = 2
NO_OF_QUARTERS: int = 1
OUTPUT_DIR: Path = Path(".do_not_commit") 
SERVER: str = "ukrdc_staging"
OUTPUT_FILE: Path = Path(f"krt_demog_{SERVER}_{YEAR_START}.csv")
#OUTPUT_FILE: Path = Path(f"krt_debug.csv")

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


CENTRES = ["RFBAK"]

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
            krt_prevalent_reports = []
            for centre in CENTRES:
                # Extract incident and label
                krt_incident_report = krt_incident(session, centre, quarter_end, quarter_start)
                
        
                #krt_incident_report = vascular_access(krt_incident_report, session, quarter_end)
                krt_incident_report["incidprev"] = "incident"
                krt_incident_reports.append(krt_incident_report)

                # Extract prevalent and label   
                krt_prevalent_report = krt_prevalent(session, centre, quarter_end)
                #krt_prevalent_report = vascular_access(krt_prevalent_report, session, quarter_end)

                krt_prevalent_report["incidprev"] = "prevalent"
                krt_prevalent_reports.append(krt_prevalent_report)
                
            krt_incident_combined = pd.concat(krt_incident_reports, ignore_index=True) if krt_incident_reports else pd.DataFrame()
            krt_prevalent_combined = pd.concat(krt_prevalent_reports, ignore_index=True) if krt_prevalent_reports else pd.DataFrame()
                
            # add some labels 
            combined = age(pd.concat([krt_prevalent_combined, krt_incident_combined]), quarter_end)
            combined = ethnicity(combined, session)
            combined = sex(combined)
            combined = adult_paed(session, combined)
            combined = imd(session, combined)
            combined["year"] = current_year
            combined["quarter"] = current_quarter

            quarterly_reports.append(combined)

    combined = pd.concat(quarterly_reports)

    # export unaggregated 
    combined.to_csv(OUTPUT_DIR / f"{OUTPUT_FILE.stem}_unaggregated_{OUTPUT_FILE.suffix}", index=False)

    # export aggregated 
    combined["dialtplt_dup"] = combined["dialtplt"]
    output = aggregate_data(
        cohort_wide = combined,
        column_attributes = [
            "centre_code",
            "satellite_code",
            "adult_paed",
            "dialtplt_dup",
            "year",
            "quarter",
            "incidprev"
        ],
        row_attributes = [
            "age",
            "imddecile",
            "dialtplt",
            "ethnicity",
            "sex"
        ]     
    )
    output.rename(columns = {"dialtplt_dup": "dialtplt"}, inplace=True)
    
    # save to csv
    output.to_csv(OUTPUT_DIR / OUTPUT_FILE, index=False)

    print(output.head())


if __name__ == "__main__":
    main()