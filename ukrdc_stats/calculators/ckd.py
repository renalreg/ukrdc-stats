"""Prevalent CKD stats calculator.

Recreates the PrevalentCKDCalculator interface used by the UKRDC API on top
of the version 3.0.0 cohort functions. The care planning report and XML
archive plumbing have been retired; the calculator now returns demographic
breakdowns of the prevalent CKD cohort.
"""

import datetime as dt

import pandas as pd
from sqlalchemy.orm import Session

from ukrdc_stats.calculators.builders import build_labelled2d
from ukrdc_stats.calculators.models import (
    BaseTable,
    CKDMetadata,
    CKDStats,
    UnitLevelCKDStats,
)
from ukrdc_stats.cohorts.base import ckd_prevalent
from ukrdc_stats.descriptions import careplanning_descriptions, demographic_descriptions
from ukrdc_stats.exceptions import NoCohortError
from ukrdc_stats.labellers.clinical import prevalent_careplanning
from ukrdc_stats.utils.data import aggregate_data

COLUMN_ATTRIBUTES = ["satellite_code"]
ROW_ATTRIBUTES = ["age", "ethnicity", "sex", "assessmentoutcome"]


class PrevalentCKDCalculator:
    """Calculates demographic statistics for the prevalent CKD cohort of a
    renal centre at a given prevalence point.
    """

    def __init__(
        self,
        session: Session,
        facility: str,
        prevalence_point: dt.datetime | None = None,
    ):
        """Args:
        session (Session): UKRDC database session.
        facility (str): Renal centre code.
        prevalence_point (Optional[dt.datetime]): Date the cohort is
            calculated at. Defaults to now.
        """
        self.session = session
        self.facility = facility
        self.prevalence_point = prevalence_point or dt.datetime.now()

        self._patient_cohort: pd.DataFrame | None = None
        self._aggregated: pd.DataFrame | None = None

    def extract_patient_cohort(self) -> None:
        """Extract the prevalent CKD cohort and apply the same labelling pipeline
        as the extract_ckd_prevalent.py script: IMD deprivation decile and
        prevalent careplanning assessment outcome.
        """
        cohort = ckd_prevalent(self.session, self.facility, self.prevalence_point)
        if cohort.empty:
            raise NoCohortError(
                f"No patients found in the cohort. Did you mean to try and extract facility {self.facility}?"
            )

        cohort = prevalent_careplanning(
            self.session, cohort, self.prevalence_point, "TPLTassess"
        )

        cohort["ethnicity"] = cohort["ukkaethnicity"].fillna("Missing")

        self._patient_cohort = cohort

    @property
    def aggregated(self) -> pd.DataFrame:
        """Long format headcounts split by satellite unit. Calculated once and
        reused by all the stats builders.
        """
        if self._aggregated is None:
            if self._patient_cohort is None:
                self.extract_patient_cohort()
            self._aggregated = aggregate_data(
                cohort_wide=self._patient_cohort,
                column_attributes=COLUMN_ATTRIBUTES,
                row_attributes=ROW_ATTRIBUTES,
            )
        return self._aggregated

    def _population(self, unit: str = "all") -> int:
        cohort = self._patient_cohort
        if unit != "all":
            cohort = cohort[cohort["satellite_code"] == unit]
        return cohort["ukrdcid"].nunique()

    def extract_satellite_stats(self, unit: str = "all") -> CKDStats:
        """Build the CKDStats for a single satellite unit, or for the whole
        centre by re-aggregating over the satellite split.

        Args:
            unit (str): Satellite code, or "all" for the whole centre.

        Returns:
            CKDStats: Demographic stats for the prevalent CKD cohort.
        """
        filters = {} if unit == "all" else {"satellite_code": unit}

        return CKDStats(
            prevalent_ckd_age=build_labelled2d(
                self.aggregated,
                "age",
                "Prevalent CKD Age",
                "Age breakdown of prevalent CKD patients",
                demographic_descriptions["AGE_DESCRIPTION"],
                filters,
            ),
            prevalent_ckd_ethnicity=build_labelled2d(
                self.aggregated,
                "ethnicity",
                "Prevalent CKD Ethnicity",
                "Ethnicity breakdown of prevalent CKD patients",
                demographic_descriptions["ETHNIC_GROUP_DESCRIPTION"],
                filters,
            ),
            prevalent_ckd_sex=build_labelled2d(
                self.aggregated,
                "sex",
                "Prevalent CKD Sex",
                "Sex breakdown of prevalent CKD patients",
                demographic_descriptions["GENDER_DESCRIPTION"],
                filters,
            ),
            prevalent_ckd_careplanning=build_labelled2d(
                self.aggregated,
                "assessmentoutcome",
                "Prevalent CKD Careplanning",
                "Careplanning assessment outcome of prevalent CKD patients",
                careplanning_descriptions["CKD_CAREPLANNING"],
                filters,
            ),
            metadata=CKDMetadata(
                population=self._population(unit),
                prevalence_point=self.prevalence_point,
            ),
        )

    def extract_stats(self) -> UnitLevelCKDStats:
        """Extract demographic stats for the whole centre and each satellite unit.

        Returns:
            UnitLevelCKDStats: Stats for the whole centre and per unit.
        """
        units = sorted(self.aggregated["satellite_code"].dropna().unique())
        unit_stats: dict[str, CKDStats] = {
            unit: self.extract_satellite_stats(unit) for unit in units
        }

        return UnitLevelCKDStats(all=self.extract_satellite_stats(), units=unit_stats)

    def generate_cohort_report(self) -> BaseTable:
        """Return the aggregated long format headcounts, matching the shape of
        the CSV extract scripts.

        Returns:
            BaseTable: Aggregated headcounts table.
        """
        return BaseTable(
            headers=self.aggregated.columns.tolist(),
            rows=[row.tolist() for _, row in self.aggregated.iterrows()],
        )
