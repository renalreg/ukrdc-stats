"""
PBM 13-11-26 - Report on the completeness of the first seen date of CKD
prevalent patients.
"""

import datetime as dt
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import ckd_prevalent
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.labellers.clinical import link_first_seen
from ukrdc_stats.utils.database import get_sessionmaker

SERVER = "ukrdc_live"
CENTRES = [
    # live
    "RAJ",  # MSE
    "RAQ01",  # Lister
    "RCSLB",  # Nottingham
    "RH8",  # RD&E
    "RHW01",  # Reading
    "RK7CC",  # Sheffield
    "RL403",  # Wolverhampton
    "RNJ00",  # Barts
    "RFPFG",  # Derby
    "RBD01",  # Dorset
    "RLZ01",  # Shrewsbury
    "RP5",  # Doncaster
    "99RQR13",
    "RAE05",
    "RCB55",
    "RF201",
    "RQR13",
]

OUTFILE = Path(".do_not_commit") / "ckd_prevalent.csv"

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )

prevalence_date = dt.datetime(2024, 12, 31)
completeness = []

with get_sessionmaker(SERVER, keypath=KEYPATH)() as session:
    completeness_data = []
    for centre in CENTRES:
        # extract ckd and link the first seen date
        try:
            base_cohort = ckd_prevalent(session, centre, prevalence_date)
        except EmptyCohortError:
            pass

        else:
            if not base_cohort.empty:
                base_cohort = link_first_seen(session, base_cohort)
                total_patients = len(base_cohort)
                completeness = (
                    len(base_cohort[base_cohort["first_seen"].notna()]) / total_patients
                )
                akc = len(base_cohort[base_cohort["clinictype"] == "AKC"])
                neph = len(base_cohort[base_cohort["clinictype"] == "NEPH"])
                # other = len(base_cohort[base_cohort["clinictype"]=='Other'])

                print(
                    f"{centre}: patients = {total_patients}, NEPH = {neph}, AKC = {akc}, completeness = {completeness}"
                )
                completeness_data.append(
                    {
                        "centre": centre,
                        "ckd patients": total_patients,
                        "AKC patients": akc,
                        "NEPH patients": neph,
                        "completeness %": 100.0 * completeness,
                    }
                )
                continue

        print(f"{centre}: empty")
        completeness_data.append(
            {
                "centre": centre,
                "ckd patients": 0,
                "AKC patients": 0,
                "NEPH patients": 0,
                "completeness %": 100,
            }
        )


report = pd.DataFrame(completeness_data)

agg_row = report.sum()
agg_row["centre"] = "All"
agg_row["ckd patients"] = agg_row["ckd patients"].astype(int)
agg_row["AKC patients"] = agg_row["AKC patients"].astype(int)
agg_row["NEPH patients"] = agg_row["NEPH patients"].astype(int)
agg_row["completeness %"] = (
    report["completeness %"] * report["ckd patients"]
).sum() / agg_row["ckd patients"]


report = pd.concat([report, pd.DataFrame([agg_row])], ignore_index=True)
report["completeness %"] = report["completeness %"].round().astype(int)
report.to_csv(Path(".do_not_commit") / "ckd_first_seen.csv", index=False)
