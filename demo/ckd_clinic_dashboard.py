"""Marimo demo for CKD counts by clinic type.

Select a centre and start quarter, then press Go. Missing data for the next
eight quarters is calculated from the UKRDC and appended to the aggregate CSV cache.

Run with: marimo run demo/ckd_clinic_dashboard.py
"""

import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import datetime as dt
    import re
    from pathlib import Path

    import marimo as mo
    import pandas as pd
    import plotly.express as px
    from dotenv import dotenv_values
    from sqlalchemy import select
    from ukrdc_sqla.ukrdc import SatelliteMap

    from ukrdc_stats.cohorts.base import ckd_prevalent
    from ukrdc_stats.exceptions import EmptyCohortError
    from ukrdc_stats.labellers.demographics import ethnicity
    from ukrdc_stats.labellers.geography import adult_paed, imd
    from ukrdc_stats.utils.data import aggregate_data
    from ukrdc_stats.utils.database import get_sessionmaker

    config = dotenv_values(".env")
    KEYPATH = config.get("UKRDC_STATS_KEYPATH")
    SERVER = "ukrdc_live"
    OUTPUT_DIR = Path(".do_not_commit")
    DATA_PATH = OUTPUT_DIR / "ckd_demog_ukrdc_live_2024.csv"
    CSV_COLUMNS = [
        "attribute",
        "variable",
        "centre_code",
        "satellite_code",
        "adult_paed",
        "clinictype",
        "year",
        "quarter",
        "count",
    ]
    FALLBACK_CENTRES = [
        "RAJ",
        "RAQ01",
        "RCSLB",
        "RH8",
        "RHW01",
        "RK7CC",
        "RL403",
        "RNJ00",
        "RFPFG",
        "RBD01",
        "RLZ01",
        "RP5",
        "99RQR13",
        "RAE05",
        "RCB55",
        "RF201",
        "RQR13",
    ]

    def parse_quarter(text: str) -> tuple[int, int]:
        """Parse quarters written as '2024 Q1' or 'Q1 2024'."""
        match = re.fullmatch(
            r"\s*(?:(\d{4})\s*Q([1-4])|Q([1-4])\s*(\d{4}))\s*",
            text,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise ValueError("Use the format '2024 Q1' or 'Q1 2024'")
        year = int(match.group(1) or match.group(4))
        quarter = int(match.group(2) or match.group(3))
        return year, quarter

    def end_of_quarter(year: int, quarter: int) -> dt.datetime:
        """Match the prevalence-point convention used by extract_ckd_demog.py."""
        if quarter < 4:
            return dt.datetime(year, quarter * 3 + 1, 1) - dt.timedelta(days=1)
        return dt.datetime(year, 12, 31)

    def read_extract() -> pd.DataFrame:
        """Read the aggregate CSV, returning an empty frame when absent."""
        if not DATA_PATH.exists():
            return pd.DataFrame(columns=CSV_COLUMNS)
        return pd.read_csv(DATA_PATH)

    def quarter_sequence(
        start_year: int, start_quarter: int, count: int = 8
    ) -> list[tuple[int, int]]:
        """Return consecutive calendar quarters, counting forward from the input."""
        return [
            (
                start_year + (start_quarter - 1 + offset) // 4,
                (start_quarter - 1 + offset) % 4 + 1,
            )
            for offset in range(count)
        ]

    def calculate_centre_periods(
        centre: str, periods: list[tuple[int, int]]
    ) -> pd.DataFrame:
        """Calculate centre/quarters using the same pipeline as the extract."""
        if KEYPATH is None:
            raise RuntimeError(
                "UKRDC_STATS_KEYPATH is not set, so missing data cannot be calculated"
            )

        outputs = []
        with get_sessionmaker(SERVER, keypath=KEYPATH, caching=False)() as session:
            for year, quarter in periods:
                quarter_end = end_of_quarter(year, quarter)
                try:
                    cohort = ckd_prevalent(
                        session, centre, quarter_end, egfr_mode="last"
                    )
                except EmptyCohortError:
                    continue

                if cohort.empty:
                    continue

                cohort = ethnicity(cohort, session)
                cohort = adult_paed(session, cohort)
                cohort = imd(session, cohort)
                cohort["year"] = year
                cohort["quarter"] = quarter
                outputs.append(
                    aggregate_data(
                        cohort_wide=cohort,
                        column_attributes=[
                            "centre_code",
                            "satellite_code",
                            "adult_paed",
                            "clinictype",
                            "year",
                            "quarter",
                        ],
                        row_attributes=["age", "imddecile", "ethnicity", "sex"],
                    )
                )

        if not outputs:
            return pd.DataFrame(columns=CSV_COLUMNS)
        return pd.concat(outputs, ignore_index=True)

    def upsert_extract(new_rows: pd.DataFrame) -> pd.DataFrame:
        """Append rows to the CSV, replacing duplicate aggregate keys."""
        existing = read_extract()
        combined = pd.concat([existing, new_rows], ignore_index=True)
        key_columns = [column for column in CSV_COLUMNS if column != "count"]
        combined = combined.drop_duplicates(subset=key_columns, keep="last")
        combined = combined.reindex(columns=CSV_COLUMNS)
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(DATA_PATH, index=False)
        return combined

    def database_centres() -> list[str]:
        """Read main centre codes from the satellite map, if the database is available."""
        if KEYPATH is None:
            return []
        try:
            with get_sessionmaker(SERVER, keypath=KEYPATH, caching=False)() as session:
                rows = session.execute(
                    select(SatelliteMap.main_unit_code).distinct()
                ).all()
            return sorted(row[0] for row in rows if row[0])
        except Exception:
            return []

    return (
        DATA_PATH,
        FALLBACK_CENTRES,
        KEYPATH,
        calculate_centre_periods,
        database_centres,
        mo,
        parse_quarter,
        pd,
        px,
        quarter_sequence,
        read_extract,
        upsert_extract,
    )


@app.cell
def _(DATA_PATH, FALLBACK_CENTRES, database_centres, mo, pd, read_extract):
    cached_centres = []
    cached_data = read_extract()
    if "centre_code" in cached_data.columns:
        cached_centres = (
            cached_data["centre_code"].dropna().astype(str).unique().tolist()
        )

    centre_options = sorted(
        set(FALLBACK_CENTRES) | set(cached_centres) | set(database_centres())
    )
    if not centre_options:
        centre_options = [""]

    form = (
        mo.md(
            """
            **Centre**

            {centre}

            **Quarter**

            {quarter}
            """
        )
        .batch(
            centre=mo.ui.dropdown(
                options=centre_options,
                value=centre_options[0],
                label="Centre",
                searchable=True,
                allow_select_none=False,
            ),
            quarter=mo.ui.text(
                value="2024 Q1",
                label="Start quarter (YYYY Qn)",
            ),
        )
        .form(submit_button_label="Go", bordered=False)
    )

    mo.vstack(
        [
            mo.md(f"CSV cache: `{DATA_PATH}`"),
            form,
        ]
    )
    return (form,)


@app.cell
def _(
    calculate_centre_periods,
    form,
    mo,
    parse_quarter,
    pd,
    px,
    quarter_sequence,
    read_extract,
    upsert_extract,
):
    mo.stop(
        form.value is None,
        mo.md("Choose a centre and start quarter, then press **Go**."),
    )

    centre = str(form.value["centre"]).strip()
    try:
        start_year, start_quarter = parse_quarter(str(form.value["quarter"]))
    except ValueError as error:
        mo.stop(True, mo.md(f"**Invalid quarter:** {error}"))

    periods = quarter_sequence(start_year, start_quarter, 8)
    data = read_extract()
    centre_data = data[data["centre_code"].astype(str) == centre]
    existing_periods = set(zip(centre_data["year"], centre_data["quarter"]))
    missing_periods = [period for period in periods if period not in existing_periods]

    calculated_periods = 0
    if missing_periods:
        try:
            new_rows = calculate_centre_periods(centre, missing_periods)
        except Exception as error:
            mo.stop(
                True,
                mo.md(f"**Calculation failed:** {type(error).__name__}: {error}"),
            )

        if not new_rows.empty:
            data = upsert_extract(new_rows)
            calculated_periods = (
                new_rows[["year", "quarter"]].drop_duplicates().shape[0]
            )

    period_filter = pd.DataFrame(periods, columns=["year", "quarter"])
    centre_rows = data[
        (data["centre_code"].astype(str) == centre) & (data["attribute"] == "sex")
    ].merge(period_filter, on=["year", "quarter"])
    if centre_rows.empty:
        mo.stop(
            True,
            mo.md(f"**No CKD cohort data found** for `{centre}` in these 8 quarters."),
        )

    # Counting one demographic attribute avoids repeating patients once per
    # attribute in the long-format extract; sex is complete for this cohort.
    count_rows = (
        centre_rows.groupby(["year", "quarter", "clinictype"], dropna=False)["count"]
        .sum()
        .reset_index()
    )
    clinic_types = ["AKC", "NEPH", "Other"]
    period_grid = pd.DataFrame(
        [
            (year, quarter, clinic_type)
            for year, quarter in periods
            for clinic_type in clinic_types
        ],
        columns=["year", "quarter", "clinictype"],
    )
    clinic_counts = period_grid.merge(
        count_rows, on=["year", "quarter", "clinictype"], how="left"
    )
    clinic_counts["count"] = clinic_counts["count"].fillna(0).astype(int)
    clinic_counts["period"] = (
        clinic_counts["year"].astype(str) + " Q" + clinic_counts["quarter"].astype(str)
    )
    clinic_counts["clinictype"] = pd.Categorical(
        clinic_counts["clinictype"], categories=clinic_types, ordered=True
    )
    clinic_counts = clinic_counts.sort_values(["year", "quarter", "clinictype"])
    period_labels = [f"{year} Q{quarter}" for year, quarter in periods]

    fig = px.bar(
        clinic_counts,
        x="period",
        y="count",
        color="clinictype",
        category_orders={"period": period_labels, "clinictype": clinic_types},
        labels={
            "period": "Quarter",
            "count": "Patient count",
            "clinictype": "Clinic type",
        },
        title=f"CKD patients at {centre} by clinic type",
    )
    fig.update_layout(barmode="stack", legend_title_text="Clinic type")

    if calculated_periods:
        status = (
            f"Calculated and added {calculated_periods} missing quarter(s) "
            f"for `{centre}`; showing 8 quarters from {start_year} Q{start_quarter}."
        )
    elif missing_periods:
        status = (
            f"No new rows were produced for the missing quarters; showing 8 quarters "
            f"from {start_year} Q{start_quarter}."
        )
    else:
        status = (
            f"Using cached `{centre}` data; showing 8 quarters from "
            f"{start_year} Q{start_quarter}."
        )
    mo.vstack(
        [
            mo.md(status),
            fig,
            mo.ui.table(clinic_counts[["period", "clinictype", "count"]]),
        ]
    )


if __name__ == "__main__":
    app.run()
