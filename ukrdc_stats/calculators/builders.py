"""
Shared functions which turn the long-format output of aggregate_data into the
Labelled2d models served by the API. Both the KRT and CKD calculators use
these so the pivot logic only lives in one place.
"""

from typing import Optional

import pandas as pd

from ukrdc_stats.calculators.models import (
    Labelled2d,
    Labelled2dData,
    Labelled2dMetadata,
)


def build_labelled2d(
    aggregated: pd.DataFrame,
    attribute: str,
    title: str,
    summary: str,
    description: str,
    filters: Optional[dict] = None,
) -> Labelled2d:
    """
    Pivot one attribute of an aggregated long dataframe into a Labelled2d.

    Args:
        aggregated (pd.DataFrame): Long format output of aggregate_data with
            attribute/variable/count columns plus any column attributes.
        attribute (str): Row attribute to pivot e.g. "age".
        title (str): Title for the Labelled2d metadata.
        summary (str): Summary for the Labelled2d metadata.
        description (str): Description for the Labelled2d metadata.
        filters (Optional[dict]): Column attribute values to filter on before
            pivoting e.g. {"incidprev": "incident", "satellite_code": "RFDOG"}.
            Omitting a column attribute sums the counts over it, which is how
            the "all units" stats are produced.

    Returns:
        Labelled2d: Counts of each variable of the attribute.
    """

    filtered = aggregated[aggregated["attribute"] == attribute]
    for column, value in (filters or {}).items():
        filtered = filtered[filtered[column] == value]

    # re-aggregating collapses any column attributes not pinned by the filters
    counts = (
        filtered.groupby("variable", dropna=False, observed=True)["count"]
        .sum()
        .sort_index()
    )

    return Labelled2d(
        metadata=Labelled2dMetadata(
            title=title,
            summary=summary,
            description=description,
            population_size=int(counts.sum()),
        ),
        data=Labelled2dData(
            x=[str(label) for label in counts.index],
            y=[int(count) for count in counts.values],
        ),
    )


def under_maintenance(title: str) -> Labelled2d:
    """
    Placeholder for stats which have been retired but whose fields are kept
    for API compatibility.

    Args:
        title (str): Title of the retired statistic.

    Returns:
        Labelled2d: Empty dataset flagged as under maintenance.
    """

    return Labelled2d(
        metadata=Labelled2dMetadata(
            title=title[:40],
            summary="This statistic is under maintenance",
            description=(
                "This statistic is under maintenance and is not currently "
                "calculated. The field is retained for API compatibility."
            ),
            population_size=None,
        ),
        data=Labelled2dData(x=[], y=[]),
    )
