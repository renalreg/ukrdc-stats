"""
PBM 13-11-26 Script to generate a ukrdc incidence list for a given year to
remove from the akc alerts.

Centres which exist on ukrdc_live are extracted from there; any centre not
on live falls back to ukrdc_staging. Output contains NHS numbers so it is
written to the git-ignored .do_not_commit folder.
"""

import datetime as dt
from pathlib import Path

from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import krt_incident
from ukrdc_stats.exceptions import EmptyCohortError, InvalidCentreError
from ukrdc_stats.labellers.base import nhs_number
from ukrdc_stats.utils.data import main_centres
from ukrdc_stats.utils.database import get_sessionmaker

YEAR = 2025
OUTFILE = Path(".do_not_commit") / f"krt_incidence_{YEAR}_for_aki.csv"

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )


start_date = dt.datetime(YEAR, 1, 1)
# last microsecond of the year so timelines starting on 31 Dec are included
end_date = dt.datetime(YEAR + 1, 1, 1) - dt.timedelta(microseconds=1)


with get_sessionmaker("ukrdc_live", keypath=KEYPATH)() as session:
    centres = main_centres(session)

# centres = centres
extracted_centres: list[str] = []
total = 0

OUTFILE.parent.mkdir(parents=True, exist_ok=True)
# start a fresh file with the header row; centres are appended as extracted
OUTFILE.write_text("modality,startdate,nhsno,main_centre\n")


for server in ["ukrdc_live", "ukrdc_staging"]:
    with get_sessionmaker(server, keypath=KEYPATH)() as session:
        for centre in centres:
            # skip centres already extracted from live or absent on this server
            if centre in extracted_centres:
                print(f"{centre} already extracted for live")
                continue
            try:
                cohort = krt_incident(session, centre, end_date, start_date)
            except (EmptyCohortError, InvalidCentreError):
                print(f"skipping {centre} on {server}")
                continue

            # centre exists on this server so it is handled either way
            extracted_centres.append(centre)
            if cohort.empty:
                print(f"{centre} ({server}): 0 incident patients")
                continue

            # attach nhs number then shape the output columns
            cohort = nhs_number(cohort, session)
            cohort = cohort.rename(
                columns={
                    "dialtplt": "modality",
                    "timeline_start": "startdate",
                    "nhs_number": "nhsno",
                    "centre_code": "main_centre",
                }
            )
            output = cohort[["modality", "startdate", "nhsno", "main_centre"]]
            output.to_csv(OUTFILE, mode="a", header=False, index=False)
            total += len(output)
            print(f"{centre} ({server}): {len(output)} incident patients")

print(f"Wrote {total} incident patients to {OUTFILE}")

not_extracted = [centre for centre in centres if centre not in extracted_centres]
if not_extracted:
    print(f"Centres not found on either server: {','.join(not_extracted)}")
