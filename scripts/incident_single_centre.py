"""This is a manual test to check the totals calculated by the calculator
functions against know totals from UKKA published ckd demographics data.
"""

import datetime as dt
from pathlib import Path

from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import krt_incident
from ukrdc_stats.utils.data import aggregate_data
from ukrdc_stats.utils.database import get_sessionmaker

FACILITY = "RCSLB"
SERVER = "ukrdc_live"
YEAR = 2023

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

REF_DATA_PATH = Path("tests/test_statistical_accuracy/data/demog_2023.xlsx")


if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )

start_date = dt.datetime(2023, 1, 1)
end_date = dt.datetime(2023, 12, 31)

with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
    centre_cohort = krt_incident(
        session,
        FACILITY,
        start_date=dt.datetime(2023, 1, 1),
        end_date=dt.datetime(2023, 12, 31),
    )
    centre_cohort = centre_cohort[centre_cohort["centre_code"] == FACILITY]
    aggregate_data = aggregate_data(
        centre_cohort,
        ["centre_code", "satellite_code", "admitreasoncode"],
        ["dialtplt"],
    )
    # aggregate_data = aggregate_data(centre_cohort, ["centre_code", "satellite_code", "group1", "group2", "group3", "group4", "ckd_centre", "admitreasoncode"], ["dialtplt"])
    aggregate_data.to_csv(f".do_not_commit/{FACILITY}_incident_{YEAR}.csv", index=False)
