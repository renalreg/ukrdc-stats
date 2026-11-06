"""Marimo dashboard for exploring the extract_krt_demog.py CSV output.

Filters the long-format counts like an Excel pivot table, then shows a
stacked bar chart (built from the pivot) with the pivot table beneath.

Run with: marimo run demo/krt_dashboard.py
"""

import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    from pathlib import Path

    import marimo as mo
    import pandas as pd
    import plotly.express as px

    DATA_PATH: Path = (
        mo.notebook_dir().parent / ".do_not_commit" / "krt_demog_ukrdc_live_2024.csv"
    )
    df: pd.DataFrame = pd.read_csv(DATA_PATH)
    return df, mo, pd, px


@app.cell
def _(df: "pd.DataFrame", mo):
    # Options come from the data itself so the widgets track whatever the
    # extract produced rather than a hardcoded list.
    incidprev = mo.ui.dropdown(
        options=sorted(df["incidprev"].dropna().unique()),
        value="prevalent",
        label="Incident / prevalent",
    )
    attribute = mo.ui.dropdown(
        options=sorted(df["attribute"].dropna().unique()),
        value="age",
        label="Demographic attribute",
    )
    centre = mo.ui.multiselect(
        options=sorted(df["centre_code"].dropna().unique()),
        value=sorted(df["centre_code"].dropna().unique()),
        label="Centre",
    )
    treatment = mo.ui.multiselect(
        options=sorted(df["dialtplt"].dropna().unique()),
        value=sorted(df["dialtplt"].dropna().unique()),
        label="Treatment (KRT type)",
    )
    quarter = mo.ui.multiselect(
        options=sorted(
            (df["year"].astype(str) + " Q" + df["quarter"].astype(str)).unique()
        ),
        value=sorted(
            (df["year"].astype(str) + " Q" + df["quarter"].astype(str)).unique()
        ),
        label="Quarter",
    )
    mo.hstack(
        [incidprev, attribute, centre, treatment, quarter],
        wrap=True,
        gap=1,
    )
    return attribute, centre, incidprev, quarter, treatment


@app.cell
def _(
    attribute,
    centre,
    df: "pd.DataFrame",
    incidprev,
    pd,
    quarter,
    treatment,
):
    # Each widget contributes one boolean mask, mirroring an Excel pivot
    # table's filter pane. Selecting one attribute at a time stops patients
    # being counted once per demographic dimension.
    year_quarter: pd.Series = df["year"].astype(str) + " Q" + df["quarter"].astype(str)
    filtered: pd.DataFrame = df[
        (df["incidprev"] == incidprev.value)
        & (df["attribute"] == attribute.value)
        & df["centre_code"].isin(centre.value)
        & df["dialtplt"].isin(treatment.value)
        & year_quarter.isin(quarter.value)
    ]
    return (filtered,)


@app.cell
def _(filtered: "pd.DataFrame", pd):
    # Summing over the remaining dimensions keeps the pivot consistent no
    # matter which filters are active.
    pivot: pd.DataFrame = filtered.pivot_table(
        index="centre_code",
        columns="variable",
        values="count",
        aggfunc="sum",
        fill_value=0,
    )
    return (pivot,)


@app.cell
def _(mo, pivot, px):
    # Plotting the pivot directly guarantees the chart and table always
    # show identical numbers.
    fig = px.bar(
        pivot,
        x=pivot.index,
        y=list(pivot.columns),
        labels={"value": "Patient count", "centre_code": "Centre", "variable": ""},
        title="KRT patient counts by centre",
    )
    fig.update_layout(barmode="stack", legend_title_text="")
    mo.vstack([fig, mo.ui.table(pivot.reset_index())])
    return


if __name__ == "__main__":
    app.run()
