"""
PBM -13-11-26 Simple report to extract the careplanning data held in the ukrdc
for the midlands region
"""

import datetime as dt
from pathlib import Path

from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import ckd_prevalent
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.labellers.base import nhs_number
from ukrdc_stats.labellers.clinical import link_first_seen, prevalent_careplanning
from ukrdc_stats.utils.data import lookup_codes
from ukrdc_stats.utils.database import get_sessionmaker

CENTRES = [
    "RRK02",  # Bham
    "RKB01",  # Covnt
    "RFPFG",  # Derby
    "RNA03",  # Dudley
    "RFBAK",  # Leic
    "RCSLB",  # Nottm
    "RLZ01",  # Shrew
    "RJE01",  # Stoke
    "RL403",  # Wolve
]

OUTFILE = Path(".do_not_commit") / "kquip" / "ckd_careplanning.csv"

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")


def custom_ckd_cohort(session, centre, prevalence_date, censor=False):
    """
    Function to customise basic ckd cohort based on custom criterion, in this
    case adding a column and censoring the result.
    """
    days_since_first_seen = 30

    base_cohort = ckd_prevalent(session, centre, prevalence_date)
    base_cohort = link_first_seen(session, base_cohort)

    # censor data based on first seen
    if censor:
        base_cohort["days_since_first_seen"] = (
            prevalence_date - base_cohort["first_seen"]
        ).dt.days
        base_cohort = base_cohort[
            base_cohort["days_since_first_seen"] <= days_since_first_seen
        ]

    return base_cohort


if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )

prevalence_date = dt.datetime.now()
extracted_centres = []

for server in ["ukrdc_live", "ukrdc_staging"]:
    with get_sessionmaker(server, keypath=KEYPATH)() as session:
        rr_51_desc = lookup_codes("RR51", "description", session)
        for centre in CENTRES:
            if centre in extracted_centres:
                # skip to next centre if already extracted
                continue

            # extract ckd and link the first seen date
            try:
                # Extract patient row level data
                base_cohort = custom_ckd_cohort(session, centre, prevalence_date)
            except EmptyCohortError:
                pass

            else:
                if not base_cohort.empty:
                    # add extra columns for first seen data and careplanning choice
                    # drop columns that get added in the process that we don't need
                    base_cohort = prevalent_careplanning(
                        session, base_cohort, prevalence_date, ["TPLTassess"]
                    )
                    # base_cohort = base_cohort.rename(columns = {"assessmentoutcomecode": "TRTcode"})

                    # Map outcome to desc
                    base_cohort["TRTChoice"] = base_cohort.assessmentoutcomecode.map(
                        rr_51_desc
                    )
                    base_cohort = base_cohort.drop(
                        columns=[
                            "assessmentoutcomecode",
                            "assessmenttypecode",
                            "assessmentstart",
                            "assessmentend",
                            "assessmentoutcome",
                        ]
                    )

                    base_cohort = prevalent_careplanning(
                        session, base_cohort, prevalence_date, ["KRTassess"]
                    )
                    base_cohort["KRTChoice"] = base_cohort.assessmentoutcomecode.map(
                        rr_51_desc
                    )

                    # add nhs number
                    base_cohort = nhs_number(base_cohort, session)

                    # output results
                    extracted_centres.append(centre)
                    centre_file = OUTFILE.with_name(
                        f"{OUTFILE.stem}_{centre}_{server}{OUTFILE.suffix}"
                    )
                    OUTFILE.parent.mkdir(parents=True, exist_ok=True)
                    base_cohort.to_csv(
                        centre_file,
                        columns=[
                            "ukrdcid",
                            "nhs_number",
                            "centre_code",
                            "satellite_code",
                            "clinictype",
                            "first_seen",
                            "egfr_min",
                            "TRTChoice",
                            "KRTChoice",
                        ],
                        index=False,
                    )

not_extracted = ",".join(
    [centre for centre in CENTRES if centre not in extracted_centres]
)
print(f"failed to extract centers: {not_extracted}")
