from ukrdc_stats.cohorts.base import ckd_prevalent
from ukrdc_stats.labellers.geography import imd, main_satellite_centres
from ukrdc_stats.labellers.clinical import prevalent_careplanning
from ukrdc_stats.utils.data import aggregate_data
from ukrdc_stats.utils.database import get_sessionmaker
import datetime as dt
from dotenv import dotenv_values
from pathlib import Path

SERVER = "ukrdc_live"
CENTRE = "RP5"
OUTFILE = Path(".do_not_commit") / "ckd_prevalent.csv"

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")


if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )
prevalence_date = dt.datetime(2023, 12, 31)

with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
    base_cohort = ckd_prevalent(session, CENTRE, prevalence_date)
    base_cohort = imd(session, base_cohort)
    base_cohort = prevalent_careplanning(session,base_cohort,prevalence_date, "TPLTassess")
    base_cohort = main_satellite_centres(session, base_cohort)


    column_attributes = [
        "centre_code", 
        "satellite_code", 
        "clinictype"
    ]
    row_attributes = [
        "sex",
        "age",
        "ukkaethnicity",
        "imddecile",
        "assessmentoutcome"
    ]

    # only keep needed columns
    base_cohort = base_cohort[
        ["ukrdcid"]  +   column_attributes + row_attributes  
    ].drop_duplicates()
    
    aggregated_data = aggregate_data(
        base_cohort, column_attributes
    )
    aggregated_data.to_csv(OUTFILE, index=False)
    
    
    #print(base_cohort)