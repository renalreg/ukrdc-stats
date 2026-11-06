"""Compare krt_incident calculator output against reference CSVs.

Runs the incident calculator for each (quarter, facility) pair in the
reference data, groups by modality (collapsing HHD into HD), and writes
the percentage difference to a CSV in tests/test_statistical_accuracy/output/.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import krt_incident
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.utils.data import aggregate_data
from ukrdc_stats.utils.database import get_sessionmaker

DATA_DIR = Path("tests/test_statistical_accuracy/data")
OUTPUT_DIR = Path("tests/test_statistical_accuracy/output")

# Map server name to reference CSV filename
SERVERS = {
    "ukrdc_live": "krt_incidence_live.csv",
    "ukrdc_staging": "krt_incidence_staging.csv",
}

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )


def run_comparison(server: str, ref_csv: Path) -> pd.DataFrame:
    """Run krt_incident for all facilities per quarter, concatenate the
    cohorts, aggregate with aggregate_data, and compare to the reference CSV."""
    ref_data = pd.read_csv(ref_csv)

    # Parse dates: quarter_start is dd/mm/yyyy, quarter_end is dd/mm/yyyy hh:mm
    ref_data["quarter_start"] = pd.to_datetime(
        ref_data["quarter_start"], format="%d/%m/%Y"
    )
    ref_data["quarter_end"] = pd.to_datetime(
        ref_data["quarter_end"], format="%d/%m/%Y %H:%M"
    )

    results = []
    session_maker = get_sessionmaker(server, keypath=KEYPATH)

    with session_maker() as session:
        # Unique (quarter_start, quarter_end) pairs — all facilities per quarter
        quarters = ref_data[["quarter_start", "quarter_end"]].drop_duplicates()

        for _, q in quarters.iterrows():
            q_start = q["quarter_start"]
            q_end = q["quarter_end"]

            # All facilities in this quarter
            facilities = ref_data[
                (ref_data["quarter_start"] == q_start)
                & (ref_data["quarter_end"] == q_end)
            ]["sendingfacility"].unique()

            # Fetch and concatenate all facility cohorts for this quarter
            all_cohorts = []
            for facility in facilities:
                try:
                    cohort = krt_incident(
                        session, facility, start_date=q_start, end_date=q_end
                    )
                    all_cohorts.append(cohort)
                except EmptyCohortError:
                    continue

            if not all_cohorts:
                # No data for any facility this quarter — still emit zero rows
                ref_subset = ref_data[
                    (ref_data["quarter_start"] == q_start)
                    & (ref_data["quarter_end"] == q_end)
                ]
                for _, ref_row in ref_subset.iterrows():
                    results.append(_make_row(
                        server, q_start, q_end, ref_row["sendingfacility"],
                        ref_row["first_modality_type"], int(ref_row["incidence"]), 0,
                    ))
                continue

            combined_cohort = pd.concat(all_cohorts, ignore_index=True)
            # Collapse HHD into HD
            combined_cohort = combined_cohort.copy()
            combined_cohort.loc[combined_cohort["dialtplt"] == "HHD", "dialtplt"] = "HD"

            # Aggregate: count heads by centre_code and dialtplt
            aggregated = aggregate_data(
                combined_cohort,
                column_attributes=["centre_code"],
                row_attributes=["dialtplt"],
            )
            # Filter to dialtplt rows only and pivot for easy lookup
            modality_counts = aggregated[aggregated["attribute"] == "dialtplt"]

            # Build comparison rows against reference data for this quarter
            ref_subset = ref_data[
                (ref_data["quarter_start"] == q_start)
                & (ref_data["quarter_end"] == q_end)
            ]
            for _, ref_row in ref_subset.iterrows():
                facility = ref_row["sendingfacility"]
                modality = ref_row["first_modality_type"]
                ref_count = int(ref_row["incidence"])

                match = modality_counts[
                    (modality_counts["centre_code"] == facility)
                    & (modality_counts["variable"] == modality)
                ]
                calc_count = int(match["count"].sum())

                results.append(_make_row(
                    server, q_start, q_end, facility, modality, ref_count, calc_count,
                ))

    return pd.DataFrame(results)


def _make_row(
    server: str,
    q_start: dt.datetime,
    q_end: dt.datetime,
    facility: str,
    modality: str,
    ref_count: int,
    calc_count: int,
) -> dict:
    """Build a single comparison row."""
    difference = calc_count - ref_count
    pct_diff = round(difference / ref_count * 100, 1) if ref_count else 0.0
    return {
        "server": server,
        "quarter_start": q_start.strftime("%d/%m/%Y"),
        "quarter_end": q_end.strftime("%d/%m/%Y %H:%M"),
        "sendingfacility": facility,
        "first_modality_type": modality,
        "reference_count": ref_count,
        "calculated_count": calc_count,
        "difference": difference,
        "percentage_difference": pct_diff,
    }


def main() -> None:
    all_results = []

    for server, csv_name in SERVERS.items():
        ref_csv = DATA_DIR / csv_name
        if not ref_csv.exists():
            print(f"Skipping {server}: {ref_csv} not found")
            continue

        print(f"Running comparison for {server}...")
        df = run_comparison(server, ref_csv)
        all_results.append(df)
        print(f"  {len(df)} rows compared")

    if not all_results:
        raise RuntimeError("No reference data found for any server")

    combined = pd.concat(all_results, ignore_index=True)

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / "krt_incidence_comparison.csv"
    combined.to_csv(output_path, index=False)

    print(f"\nOutput written to {output_path}")
    print(combined.to_string(index=False))
    print(":)")


if __name__ == "__main__":
    main()
