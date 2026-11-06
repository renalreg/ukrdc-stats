"""Compare krt_prevalent calculator output against reference CSVs.

Runs the prevalent calculator for each (prevalence_point, facility) pair in
the reference data, groups by modality (collapsing HHD into HD), and writes
the percentage difference to a CSV in tests/test_statistical_accuracy/output/.
"""
from pathlib import Path

import datetime as dt

import pandas as pd
from dotenv import dotenv_values

from ukrdc_stats.cohorts.base import krt_prevalent
from ukrdc_stats.exceptions import EmptyCohortError
from ukrdc_stats.utils.data import aggregate_data
from ukrdc_stats.utils.database import get_sessionmaker

DATA_DIR = Path("tests/test_statistical_accuracy/data")
OUTPUT_DIR = Path("tests/test_statistical_accuracy/output")

# Map server name to reference CSV filename
SERVERS = {
    "ukrdc_live": "krt_prevalence_live.csv",
    "ukrdc_staging": "krt_prevalence_staging.csv",
}

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")

if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )


def run_comparison(server: str, ref_csv: Path) -> pd.DataFrame:
    """Run krt_prevalent for all facilities per prevalence_point, concatenate
    the cohorts, aggregate with aggregate_data, and compare to the reference CSV."""
    ref_data = pd.read_csv(ref_csv)

    # Parse dates: prevalence_point is dd/mm/yyyy
    ref_data["prevalence_point"] = pd.to_datetime(
        ref_data["prevalence_point"], format="%d/%m/%Y"
    )

    results = []
    session_maker = get_sessionmaker(server, keypath=KEYPATH)

    with session_maker() as session:
        # Unique prevalence_points — all facilities per point
        points = ref_data[["prevalence_point"]].drop_duplicates()

        for _, p in points.iterrows():
            prev_point = p["prevalence_point"]

            # All facilities at this prevalence point
            facilities = ref_data[
                ref_data["prevalence_point"] == prev_point
            ]["sendingfacility"].unique()

            # Fetch and concatenate all facility cohorts for this prevalence point
            all_cohorts = []
            for facility in facilities:
                try:
                    cohort = krt_prevalent(session, facility, prevalence_point=prev_point)
                    all_cohorts.append(cohort)
                except EmptyCohortError:
                    continue

            if not all_cohorts:
                # No data for any facility — still emit zero rows
                ref_subset = ref_data[ref_data["prevalence_point"] == prev_point]
                for _, ref_row in ref_subset.iterrows():
                    results.append(_make_row(
                        server, prev_point, ref_row["sendingfacility"],
                        ref_row["modality"], int(ref_row["count"]), 0,
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
            modality_counts = aggregated[aggregated["attribute"] == "dialtplt"]

            # Build comparison rows against reference data for this prevalence point
            ref_subset = ref_data[ref_data["prevalence_point"] == prev_point]
            for _, ref_row in ref_subset.iterrows():
                facility = ref_row["sendingfacility"]
                modality = ref_row["modality"]
                ref_count = int(ref_row["count"])

                match = modality_counts[
                    (modality_counts["centre_code"] == facility)
                    & (modality_counts["variable"] == modality)
                ]
                calc_count = int(match["count"].sum())

                results.append(_make_row(
                    server, prev_point, facility, modality, ref_count, calc_count,
                ))

    return pd.DataFrame(results)


def _make_row(
    server: str,
    prev_point: dt.datetime,
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
        "prevalence_point": prev_point.strftime("%d/%m/%Y"),
        "sendingfacility": facility,
        "modality": modality,
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
    output_path = OUTPUT_DIR / "krt_prevalence_comparison.csv"
    combined.to_csv(output_path, index=False)

    print(f"\nOutput written to {output_path}")
    print(combined.to_string(index=False))
    print(":)")


if __name__ == "__main__":
    main()
