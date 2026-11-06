"""
KRT stats calculator.

Recreates the KRTStatsCalculator interface used by the UKRDC API on top of
the version 3.0.0 cohort and labeller functions. The output is limited to
demographic breakdowns; the dialysis session and vascular access stats the
API previously served are returned as "Under maintenance" placeholders.
"""

import datetime as dt
from typing import Dict, Optional

import pandas as pd
from sqlalchemy.orm import Session

from ukrdc_stats.calculators.builders import build_labelled2d, under_maintenance
from ukrdc_stats.calculators.models import (
    BaseTable,
    KRTMetadata,
    KRTStats,
    UnitLevelKRTStats,
)
from ukrdc_stats.cohorts.base import krt_incident, krt_prevalent
from ukrdc_stats.descriptions import (
    careplanning_descriptions,
    demographic_descriptions,
    dialysis_descriptions,
)
from ukrdc_stats.exceptions import NoCohortError
from ukrdc_stats.labellers.clinical import prevalent_careplanning
from ukrdc_stats.labellers.demographics import age, ethnicity, sex
from ukrdc_stats.utils.data import aggregate_data

COLUMN_ATTRIBUTES = ["satellite_code", "incidprev"]
ROW_ATTRIBUTES = ["age", "ethnicity", "sex", "dialtplt", "assessmentoutcome"]


class KRTStatsCalculator:
    """Calculates demographic statistics for the incident and prevalent KRT
    cohorts of a renal centre over a given time window."""

    def __init__(
        self,
        session: Session,
        facility: str,
        from_time: Optional[dt.datetime] = None,
        to_time: Optional[dt.datetime] = None,
    ):
        """
        Args:
            session (Session): UKRDC database session.
            facility (str): Renal centre code.
            from_time (Optional[dt.datetime]): Start of the incidence window.
                Defaults to a year before to_time.
            to_time (Optional[dt.datetime]): End of the incidence window and
                prevalence point. Defaults to now.
        """
        self.session = session
        self.facility = facility
        self.to_time = to_time or dt.datetime.now()
        self.from_time = from_time or self.to_time - dt.timedelta(days=365)

        if self.from_time > self.to_time:
            raise ValueError("from_time must be before to_time")

        self._patient_cohort: Optional[pd.DataFrame] = None
        self._aggregated: Optional[pd.DataFrame] = None

    def extract_patient_cohort(self) -> None:
        """
        Extract the incident and prevalent cohorts for the centre, combine
        them and label with the demographic attributes.
        """
        incident = krt_incident(
            self.session, self.facility, self.to_time, self.from_time
        )
        incident["incidprev"] = "incident"

        prevalent = krt_prevalent(self.session, self.facility, self.to_time)
        prevalent["incidprev"] = "prevalent"

        combined = pd.concat([prevalent, incident], ignore_index=True)
        if combined.empty:
            raise NoCohortError(
                f"No patients found in the cohort. Did you mean to try and extract facility {self.facility}?"
            )

        combined = age(combined, self.to_time, session=self.session)
        combined = ethnicity(combined, self.session)
        combined = sex(combined, self.session)

        combined = prevalent_careplanning(
            self.session, combined, self.to_time, "TPLTassess"
        )

        self._patient_cohort = combined

    @property
    def aggregated(self) -> pd.DataFrame:
        """
        Long format headcounts split by satellite unit and incident/prevalent
        cohort. Calculated once and reused by all the stats builders.
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

    def extract_satellite_stats(self, unit: str = "all") -> KRTStats:
        """
        Build the KRTStats for a single satellite unit, or for the whole
        centre by re-aggregating over the satellite split.

        Args:
            unit (str): Satellite code, or "all" for the whole centre.

        Returns:
            KRTStats: Demographic stats plus placeholder legacy fields.
        """
        # pinning satellite_code only for real units means the "all" stats
        # fall out of the same builders by summing over the satellite split
        unit_filter = {} if unit == "all" else {"satellite_code": unit}

        demographics = {}
        for incidprev in ("incident", "prevalent"):
            filters = {"incidprev": incidprev, **unit_filter}
            demographics[f"{incidprev}_krt_modality"] = build_labelled2d(
                self.aggregated,
                "dialtplt",
                f"{incidprev.capitalize()} KRT Modality",
                f"Modality breakdown of {incidprev} KRT patients",
                dialysis_descriptions[f"{incidprev.upper()}_KRT_COHORT"],
                filters,
            )
            demographics[f"{incidprev}_krt_age"] = build_labelled2d(
                self.aggregated,
                "age",
                f"{incidprev.capitalize()} KRT Age",
                f"Age breakdown of {incidprev} KRT patients",
                demographic_descriptions["AGE_DESCRIPTION"],
                filters,
            )
            demographics[f"{incidprev}_krt_ethnicity"] = build_labelled2d(
                self.aggregated,
                "ethnicity",
                f"{incidprev.capitalize()} KRT Ethnicity",
                f"Ethnicity breakdown of {incidprev} KRT patients",
                demographic_descriptions["ETHNIC_GROUP_DESCRIPTION"],
                filters,
            )
            demographics[f"{incidprev}_krt_sex"] = build_labelled2d(
                self.aggregated,
                "sex",
                f"{incidprev.capitalize()} KRT Sex",
                f"Sex breakdown of {incidprev} KRT patients",
                demographic_descriptions["GENDER_DESCRIPTION"],
                filters,
            )
            demographics[f"{incidprev}_krt_careplanning"] = build_labelled2d(
                self.aggregated,
                "assessmentoutcome",
                f"{incidprev.capitalize()} KRT Careplanning",
                f"Careplanning assessment outcome of {incidprev} KRT patients",
                careplanning_descriptions["KRT_CAREPLANNING"],
                filters,
            )

        return KRTStats(
            **demographics,
            incentre_dialysis_frequency=under_maintenance(
                "In-Centre Dialysis Frequency"
            ),
            incentre_time_dialysed=under_maintenance("In-Centre Time Dialysed"),
            incident_initial_access=under_maintenance("Incident Initial Access"),
            prevalent_most_recent_access=under_maintenance(
                "Prevalent Most Recent Access"
            ),
            metadata=KRTMetadata(
                population=self._population(unit),
                from_time=self.from_time,
                to_time=self.to_time,
            ),
        )

    def extract_stats(self) -> UnitLevelKRTStats:
        """
        Extract demographic stats for the whole centre and each satellite unit.

        Returns:
            UnitLevelKRTStats: Stats for the whole centre and per unit.
        """
        units = sorted(self.aggregated["satellite_code"].dropna().unique())
        unit_stats: Dict[str, KRTStats] = {
            unit: self.extract_satellite_stats(unit) for unit in units
        }

        return UnitLevelKRTStats(all=self.extract_satellite_stats(), units=unit_stats)

    def generate_cohort_report(self) -> BaseTable:
        """
        Return the aggregated long format headcounts, matching the shape of
        the CSV produced by scripts/extract_krt_demog.py.

        Returns:
            BaseTable: Aggregated headcounts table.
        """
        return BaseTable(
            headers=self.aggregated.columns.tolist(),
            rows=[row.tolist() for _, row in self.aggregated.iterrows()],
        )
