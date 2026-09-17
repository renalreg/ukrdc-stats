"""
Extract unaggregated incident KRT and prevalent CKD cohort data per centre
per quarter and populate copies of the incidence_report_template.xlsx with
patient-level data including NHS numbers.
"""

import datetime as dt
import shutil
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import ckd_prevalent, krt_incident
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.labellers.base import nhs_number
from ukrdc_stats.utils.database import get_sessionmaker


config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

YEAR_START: int = 2025
QUARTER_START: int = 2
NO_OF_QUARTERS: int = 4
OUTPUT_DIR: Path = Path(".do_not_commit")
SERVER: str = "ukrdc_live"
TEMPLATE_PATH: Path = Path("templates/incidence_report_template.xlsx")

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

KRT_KEY_COLUMNS = [
    "nhs_number",
    "nhs_number_type",
    "ukrdcid",
    "centre_code",
    "satellite_code",
    "dialtplt",
    "fromtime",
    "totime",
    "timeline_start",
    "timeline_stop",
    "deathtime",
    "admitreasoncode",
    "dischargereasoncode",
    "admissionsourcecode",
    "quarter",
    "year",
]

CKD_KEY_COLUMNS = [
    "nhs_number",
    "nhs_number_type",
    "ukrdcid",
    "centre_code",
    "satellite_code",
    "clinictype",
    "fromtime",
    "totime",
    "deathtime",
    "birthtime",
    "admitreasoncode",
    "admitreasoncodestd",
    "sex",
    "ukkaethnicity",
    "age",
    "decimalage",
    "egfr_min",
    "adult_paed",
    "quarter",
    "year",
]


def main():
    with get_sessionmaker(SERVER, keypath=KEYPATH, caching=False)() as session:
        for centre in CENTRES:
            krt_frames: list[pd.DataFrame] = []
            ckd_frames: list[pd.DataFrame] = []

            for quarter in range(
                QUARTER_START - 1, QUARTER_START + NO_OF_QUARTERS - 1
            ):
                current_quarter = (quarter % 4) + 1
                current_year = YEAR_START + quarter // 4
                quarter_start = dt.datetime(current_year, current_quarter * 3 - 2, 1)
                if current_quarter < 4:
                    quarter_end = dt.datetime(
                        current_year, current_quarter * 3 + 1, 1
                    ) - dt.timedelta(days=1)
                else:
                    quarter_end = dt.datetime(current_year, 12, 31)

                # Incident KRT cohort
                print(
                    f"  {centre} Q{current_quarter} {current_year}: "
                    f"extracting KRT...",
                    flush=True,
                )
                try:
                    krt_cohort = krt_incident(
                        session, centre, quarter_end, quarter_start
                    )
                    krt_cohort = nhs_number(krt_cohort, session, include_type=True)
                    krt_cohort["year"] = current_year
                    krt_cohort["quarter"] = current_quarter
                    krt_frames.append(krt_cohort)
                    print(f"    KRT: {len(krt_cohort)} rows", flush=True)
                except EmptyCohortError:
                    print(
                        f"    No incident KRT patients for {centre} "
                        f"Q{current_quarter} {current_year}",
                        flush=True,
                    )

                # Prevalent CKD cohort
                print(f"  {centre} Q{current_quarter} {current_year}: extracting CKD...", flush=True)
                try:
                    ckd_cohort = ckd_prevalent(session, centre, quarter_end)
                    ckd_cohort = nhs_number(ckd_cohort, session, include_type=True)
                    ckd_cohort["year"] = current_year
                    ckd_cohort["quarter"] = current_quarter
                    ckd_frames.append(ckd_cohort)
                    print(f"    CKD: {len(ckd_cohort)} rows", flush=True)
                except EmptyCohortError:
                    print(
                        f"    No CKD patients for {centre} "
                        f"Q{current_quarter} {current_year}",
                        flush=True,
                    )

            # Write Excel for this centre
            out_path = OUTPUT_DIR / f"incidence_report_{centre}.xlsx"
            shutil.copy2(TEMPLATE_PATH, out_path)

            with pd.ExcelWriter(
                out_path, mode="a", engine="openpyxl",
                if_sheet_exists="replace",
            ) as writer:
                if krt_frames:
                    krt_combined = pd.concat(krt_frames, ignore_index=True)
                    krt_output = krt_combined[KRT_KEY_COLUMNS].copy()
                    krt_output.to_excel(writer, sheet_name="incident", index=False)
                    print(f"  {centre} incident: {len(krt_output)} rows", flush=True)

                if ckd_frames:
                    ckd_combined = pd.concat(ckd_frames, ignore_index=True)
                    ckd_output = ckd_combined[CKD_KEY_COLUMNS].copy()
                    ckd_output.to_excel(writer, sheet_name="ckd", index=False)
                    print(f"  {centre} ckd: {len(ckd_output)} rows", flush=True)

            print(f"Wrote {out_path}", flush=True)

    # Refresh pivot tables in all output files via Excel COM
    refresh_pivot_tables(OUTPUT_DIR, CENTRES)


def refresh_pivot_tables(output_dir: Path, centres: list[str]) -> None:
    """Open each output file in Excel, refresh all pivot tables, and save."""
    import win32com.client

    excel = win32com.client.Dispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False

    try:
        for centre in centres:
            file_path = output_dir / f"incidence_report_{centre}.xlsx"
            if not file_path.exists():
                continue
            abs_path = str(file_path.resolve())
            wb = excel.Workbooks.Open(abs_path)
            try:
                wb.RefreshAll()
                wb.Save()
            finally:
                wb.Close(False)
            print(f"Refreshed pivots in {file_path}")
    finally:
        excel.Quit()


if __name__ == "__main__":
    main()
