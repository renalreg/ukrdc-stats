import pandas as pd
from pathlib import Path
from sqlalchemy import select, and_

from ukrdc_sqla.ukrdc import PatientRecord, PatientNumber
from ukrdc_stats.utils.database import get_sessionmaker
from dotenv import dotenv_values

server = "ukrdc_staging"

config = dotenv_values(".env")
keypath = config.get("UKRDC_STATS_KEYPATH")

data_dir = Path(".do_not_commit")
patient_data_file = data_dir / "incident_q2_2026_rfbak.csv"
stats_cohort_file = data_dir / "krt_demog_ukrdc_staging_2026_unaggregated_rfbak.csv"
outfile = data_dir / "rfbak_combined_data.xlsx"

# Load the incident CSV
df = pd.read_csv(patient_data_file, dtype=str)

with get_sessionmaker(server, keypath)() as session:
    # Link ukrdcid using patientnumber: match id_dist values against
    # PatientNumber.patientid for RFBAK sending facility
    id_map_query = (
        select(
            PatientRecord.ukrdcid,
            PatientNumber.patientid,
        )
        .join(PatientNumber, PatientRecord.pid == PatientNumber.pid)
        .where(
            and_(
                PatientNumber.patientid.in_(df["id_dist"].unique().tolist()),
                PatientRecord.sendingfacility == "RFBAK",
            )
        )
    )
    id_map = pd.DataFrame(session.execute(id_map_query))
    id_map = id_map.drop_duplicates(subset=["patientid"], keep="first")

    # Merge ukrdcid back into the incident dataframe
    df = df.merge(id_map, left_on="id_dist", right_on="patientid", how="left")
    df = df.drop(columns=["patientid"])


# Load local stats cohort CSV
stats_cohort = pd.read_csv(stats_cohort_file, dtype=str)
stats_cohort_reduced = stats_cohort[
    [
        "pid", 
        "ukrdcid", 
        "sendingfacility", 
        "centre_code", 
        "satellite_code", 
        "dialtplt", 
        "incident", 
        "timeline_start", 
        "timeline_stop", 
        "timeline_length", 
        "year", 
        "quarter"
    ]
][stats_cohort.incidprev == 'incident']


# Outer join stats cohort with incident dataframe on ukrdcid
comparison = stats_cohort_reduced.merge(df, on="ukrdcid", how="outer", indicator=True)

rfbak_local_only = comparison[comparison.pid.isna()]
rfbak_ukrdc_only = comparison[comparison.id_dist.isna()]
print(":)")