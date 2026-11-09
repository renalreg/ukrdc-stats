"""Pydantic models returned by the stats calculators.

Only the models required by the UKRDC API are kept here; they are lifted from
the ukrdc_stats/models package which existed prior to version 3.0.0. Everything
lives in one module so both calculators share a single import.
"""

import csv
import datetime as dt
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

Number = int | float | None
RowData = list[Any]


def _to_camel(snake_str: str) -> str:
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class JSONModel(BaseModel):
    # camelCase aliasing preserves the JSON shape the API serves
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class AxisLabels2d(JSONModel):
    """Generic class for any x/y axis labels
    """

    x: str | None = None
    y: str | None = None


class Basic2dMetadata(JSONModel):
    """Stats/plot metadata generic to all 2D data sets
    """

    title: str = Field(..., description="Title of plot or statistics", max_length=40)
    summary: str = Field(
        ...,
        description="Summary of what the plot or statistic is trying to achieve",
        max_length=100,
    )
    description: str = Field(
        ...,
        description="In depth description of what exactly is represented and how it has been calculated",
    )
    axis_titles: AxisLabels2d | None = Field(
        None, description="x and y labels for data"
    )
    population_size: int | None = Field(
        None,
        description="Total population size of cohort used for statistic e.g total number of patients in pie chart",
    )


class Labelled2dData(JSONModel):
    """x-y data for a labelled plot
    """

    x: list[str] = Field(..., description="list of x data points")
    y: list[Number] = Field(..., description="list of y data points")
    error_y: list[Number] | None = Field(
        None, description="Uncertainty in y data points"
    )


class Labelled2dMetadata(Basic2dMetadata):
    """Metadata for a labelled plot.

    x-axis is always a string/label, so no x-axis units are required.
    y-axis is numeric, and so units are optional here.
    """

    coding_standard_x: str | None = Field(
        None, description="UKRDC coding standard of x data points"
    )
    units_y: str | None = Field(None, description="Units of y data point")


class Labelled2d(JSONModel):
    """Return data class for a labelled plot
    """

    metadata: Labelled2dMetadata = Field(
        ..., description="Metadata for 2D data consisting of label datapoint pairs"
    )
    data: Labelled2dData = Field(
        ..., description="2D data consisting of label datapoint pairs"
    )


class BaseTable(JSONModel):
    headers: list[str] = Field(
        ..., description="Column headers describing the data contained in each row"
    )
    rows: list[RowData] = Field(
        ..., description="Rows of the table containing the data"
    )

    def to_csv(
        self, file_path: str, blank_na: bool = True, metadata: str = None
    ) -> None:
        """Serializes the BaseTable to a CSV file.

        :param file_path: The path to the file where the CSV data will be written.
        """
        with open(file_path, mode="w", newline="", encoding="utf-8") as csv_file:
            if metadata:
                csv_file.write(metadata)
            writer = csv.writer(csv_file)
            writer.writerow(self.headers)
            for row in self.rows:
                writer.writerow(
                    [item if not pd.isna(item) else "" for item in row]
                    if blank_na
                    else row
                )

    def to_pandas(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=self.headers).astype("string")


# KRT calculator models


class KRTMetadata(JSONModel):
    population: int | None = Field(
        None,
        description="Number of patients in the cohort for KRT stats calculation",
    )
    from_time: dt.datetime = Field(
        ..., description="Start time of KRT stats calculations"
    )
    to_time: dt.datetime = Field(..., description="End time of KRT stats calculations")


class KRTStats(JSONModel):
    """Container class for the KRT demographic stats. Fields previously derived
    from dialysis sessions are retained for API compatibility but only carry
    an "Under maintenance" placeholder.
    """

    incident_krt_modality: Labelled2d = Field(
        ..., description="Modality breakdown of incident KRT patients"
    )
    prevalent_krt_modality: Labelled2d = Field(
        ..., description="Modality breakdown of prevalent KRT patients"
    )
    incident_krt_age: Labelled2d = Field(
        ..., description="Age breakdown of incident KRT patients"
    )
    incident_krt_ethnicity: Labelled2d = Field(
        ..., description="Ethnicity breakdown of incident KRT patients"
    )
    incident_krt_sex: Labelled2d = Field(
        ..., description="Sex breakdown of incident KRT patients"
    )
    prevalent_krt_age: Labelled2d = Field(
        ..., description="Age breakdown of prevalent KRT patients"
    )
    prevalent_krt_ethnicity: Labelled2d = Field(
        ..., description="Ethnicity breakdown of prevalent KRT patients"
    )
    prevalent_krt_sex: Labelled2d = Field(
        ..., description="Sex breakdown of prevalent KRT patients"
    )
    incident_krt_careplanning: Labelled2d = Field(
        ...,
        description="Careplanning assessment outcome breakdown of incident KRT patients",
    )
    prevalent_krt_careplanning: Labelled2d = Field(
        ...,
        description="Careplanning assessment outcome breakdown of prevalent KRT patients",
    )
    incentre_dialysis_frequency: Labelled2d = Field(
        ..., description="Under maintenance: previously per week dialysis frequency"
    )
    incentre_time_dialysed: Labelled2d = Field(
        ..., description="Under maintenance: previously per week time dialysed"
    )
    incident_initial_access: Labelled2d = Field(
        ...,
        description="Under maintenance: previously vascular access on first session",
    )
    prevalent_most_recent_access: Labelled2d = Field(
        ..., description="Under maintenance: previously most recent vascular access"
    )
    metadata: KRTMetadata


class UnitLevelKRTStats(JSONModel):
    all: KRTStats
    units: dict[str, KRTStats]


# CKD calculator models


class CKDMetadata(JSONModel):
    population: int | None = Field(
        None,
        description="Number of patients in the prevalent CKD cohort",
    )
    prevalence_point: dt.datetime = Field(
        ..., description="Date the prevalent CKD cohort is calculated at"
    )


class CKDStats(JSONModel):
    """Container class for the prevalent CKD demographic stats
    """

    prevalent_ckd_age: Labelled2d = Field(
        ..., description="Age breakdown of prevalent CKD patients"
    )
    prevalent_ckd_ethnicity: Labelled2d = Field(
        ..., description="Ethnicity breakdown of prevalent CKD patients"
    )
    prevalent_ckd_sex: Labelled2d = Field(
        ..., description="Sex breakdown of prevalent CKD patients"
    )
    prevalent_ckd_careplanning: Labelled2d = Field(
        ...,
        description="Careplanning assessment outcome breakdown of prevalent CKD patients",
    )
    metadata: CKDMetadata


class UnitLevelCKDStats(JSONModel):
    all: CKDStats
    units: dict[str, CKDStats]
