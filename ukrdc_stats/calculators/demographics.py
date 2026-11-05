"""
Patient cohort demographics stats calculator
"""

import datetime as dt
from typing import Optional
from pydantic import Field

import pandas as pd
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from ukrdc_sqla.ukrdc import (
    Patient,
    PatientRecord,
    Treatment,
    ResultItem,
    Observation,
)
from sqlalchemy.dialects.postgresql import distinct_on

from ukrdc_stats.calculators.abc import AbstractFacilityStatsCalculator
from ukrdc_stats.exceptions import NoCohortError
from ukrdc_stats.utils import (
    aggregate_data,
    _get_satellite_list,
    AGE_BINS,
)

from ukrdc_stats.descriptions import demographic_descriptions
from ukrdc_stats.models.base import JSONModel
from ukrdc_stats.models.generic_2d import (
    AxisLabels2d,
    Labelled2d,
    Labelled2dData,
    Labelled2dMetadata,
)
from ukrdc_stats.models.reports import CohortReport


class DemographicsMetadata(JSONModel):
    population: Optional[int] = Field(
        None, description="Population demographics are calculated from"
    )


class DemographicsStats(JSONModel):
    gender: Labelled2d = Field(..., description="Gender demographic stats")
    ethnic_group: Labelled2d = Field(
        ...,
        description="Ethnicity Histogram based on the 5 ethnicity groupings used in the annual report",
    )
    age: Labelled2d = Field(..., description="Age statistics of living patients")
    metadata: DemographicsMetadata = Field(
        ..., description="Metadata describing demographic stats"
    )


class DemographicStatsCalculator(AbstractFacilityStatsCalculator):
    """Calculates the demographics information based on the personal information listed in the patient table"""

    def __init__(
        self,
        session: Session,
        facility: str,
        date: Optional[dt.datetime] = None,
        from_time: Optional[dt.datetime] = None,
        to_time: Optional[dt.datetime] = None,
    ):
        """Initialises the PatientDemographicStats class and immediately runs the relevant query

        Args:
            session (SQLAlchemy session): Connection to database to calculate statistic from.
            facility (str): Facility to calculate the
            date (datetime, optional): Date to calculate at. Defaults to today.
        """
        if to_time and date:
            date = to_time
        elif date:
            to_time = date
        else:
            date = to_time

        super().__init__(session, facility, date)

        # Set the dates to calculate between, defaulting to today and 90 days ago
        self.end_date: dt.datetime = self.date

        if not from_time:
            self.from_time = self.end_date - dt.timedelta(days=365)
        else:
            self.from_time = from_time

    def _extract_base_patient_cohort(
        self,
        include_tracing: Optional[bool] = True,
        limit_to_ukrdc: Optional[bool] = True,
        ukrr_expanded: Optional[bool] = False,
    ) -> pd.DataFrame:
        """Main database queries to produce a dataframe containing the patient demographics
        for a specified Unit.

        Args:
            include_tracing (bool, optional): Switch to use tracing rec. Defaults to False.

        Returns:
            pd.DataFrame: _description_
        """

        sats = _get_satellite_list(self.facility, self.session)

        # the following reflect criteria which are applied to the ukrr
        # quarterly extract process (i.e the criteria used to load data into
        # the renalregistry database). See here for more information:
        # https://github.com/renalreg/ukrr_quarterly_extract/blob/ec65cc06858cdabaa379e9e18b8f0614fc2c9af2/ukrr_extract/extract_functions.py#L342

        patient_query = (
            select(
                PatientRecord.pid, PatientRecord.ukrdcid, PatientRecord.sendingfacility
            )
            .ext(distinct_on(PatientRecord.ukrdcid))
            .select_from(PatientRecord)
        )

        if ukrr_expanded:
            patient_query = (
                patient_query.join(Treatment, PatientRecord.pid == Treatment.pid)
                .join(ResultItem, PatientRecord.pid == ResultItem.pid)
                .join(Observation, PatientRecord.pid == Observation.pid)
                .where(
                    or_(
                        and_(
                            Treatment.fromtime < self.start_date,
                            or_(
                                Treatment.healthcarefacilitycode.in_(sats),
                                Treatment.healthcarefacilitycode.in_(self.facility),
                            ),
                        ),
                        or_(
                            Treatment.totime >= self.end_date,
                            Treatment.totime.is_(None),
                        ),
                    ),
                    and_(
                        ResultItem.observation_time < self.start_date,  # pylint: disable=C0121
                        ResultItem.observation_time >= self.end_date,
                    ),
                    and_(
                        Observation.observation_time < self.start_date,  # pylint: disable=C0121
                        Observation.observation_time >= self.end_date,
                    ),
                )
            )
        else:
            patient_query = patient_query.join(
                Treatment, PatientRecord.pid == Treatment.pid
            ).where(
                Treatment.fromtime < self.end_date,
                or_(
                    Treatment.healthcarefacilitycode.in_(sats),
                    Treatment.healthcarefacilitycode == self.facility,
                ),
                or_(
                    Treatment.totime >= self.end_date,
                    Treatment.totime.is_(None),
                ),
            )

        # limit stats to ukrdc
        if limit_to_ukrdc:
            patient_query = patient_query.where(PatientRecord.sendingextract == "UKRDC")

        # extract patient cohort
        patients = pd.DataFrame(self.session.execute(patient_query)).drop_duplicates()
        if patients.empty:
            raise NoCohortError(
                f"No patient cohort has been extracted. Facility {self.facility} may not have a UKRDC feed."
            )

        if include_tracing:
            # Can we trace deathtime by crosslinking records in the ukrdc?
            exclude_patients = (
                select(PatientRecord.ukrdcid)
                .join(Patient, Patient.pid == PatientRecord.pid)  # type:ignore
                .where(
                    and_(
                        # PatientRecord.sendingfacility == "TRACING",
                        PatientRecord.ukrdcid.in_(
                            patients[pd.isna(patients.death_time)].ukrdcid
                        ),
                        Patient.death_time < self.end_date,
                    )
                )
            )

            exclude_patients_list = pd.DataFrame(
                self.session.execute(exclude_patients)
            ).drop_duplicates()

            # filter out patients in the exclusion list
            patients = patients[~patients.ukrdcid.isin(exclude_patients_list.ukrdcid)]

        return patients.drop_duplicates()

    def _calculate_gender(self) -> Labelled2d:
        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        gender_aggregated = aggregate_data(self._patient_cohort, ["gender"])

        return Labelled2d(
            metadata=Labelled2dMetadata(
                title="Gender Distribution",
                summary="Breakdown of patient gender identity codes",
                description=demographic_descriptions["GENDER_DESCRIPTION"],
                axis_titles=AxisLabels2d(x="Gender", y="No. of Patients"),
            ),
            data=Labelled2dData(
                x=gender_aggregated.gender.tolist(), y=gender_aggregated.value.tolist()
            ),
        )

    def _calculate_ethnic_group_code(self):
        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        ethnicity_aggregated = aggregate_data(self._patient_cohort, ["ethnicity"])

        return Labelled2d(
            metadata=Labelled2dMetadata(
                title="Ethnic Group",
                summary="Breakdown of patient ethnic group codes",
                description=demographic_descriptions["ETHNIC_GROUP_DESCRIPTION"],
                axis_titles=AxisLabels2d(x="Ethnicity", y="No. of Patients"),
            ),
            data=Labelled2dData(
                x=ethnicity_aggregated.ethnicity.tolist(),
                y=ethnicity_aggregated.value.tolist(),
            ),
        )

    def _calculate_age(self):
        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        # aggregate age and filter out deceased (this may make the total less
        # than other demographics).
        age_aggregated = aggregate_data(self._patient_cohort, ["agerange"])
        age_aggregated = age_aggregated[age_aggregated.agerange != "Deceased"]

        # Define the desired order for age ranges
        age_order = AGE_BINS["labels"]
        age_aggregated["agerange"] = pd.Categorical(
            age_aggregated["agerange"], categories=age_order, ordered=True
        )
        age_aggregated = age_aggregated.sort_values("agerange")

        return Labelled2d(
            metadata=Labelled2dMetadata(
                title="Age Distribution",
                summary="Distribution of patient ages",
                description=demographic_descriptions["AGE_DESCRIPTION"],
                axis_titles=AxisLabels2d(x="Age", y="No. of Patients"),
            ),
            data=Labelled2dData(
                x=age_aggregated.agerange.tolist(), y=age_aggregated.value.tolist()
            ),
        )

    def _extract_patient_cohort(
        self,
        include_tracing: Optional[bool] = False,
        limit_to_ukrdc: Optional[bool] = True,
        ukrr_expanded: Optional[bool] = False,
    ):
        """
        Extract a complete patient cohort dataframe to be used in stats calculations
        include_tracing switch allows patient records created by nhs tracing to be searched
        for DoD.
        """
        self._patient_cohort = self._extract_base_patient_cohort(
            include_tracing=include_tracing,
            limit_to_ukrdc=limit_to_ukrdc,
            ukrr_expanded=ukrr_expanded,
        )
        self.append_demographics()

    def extract_stats(
        self,
        include_tracing: Optional[bool] = False,
        limit_to_ukrdc: Optional[bool] = True,
        ukrr_expanded: Optional[bool] = False,
    ) -> DemographicsStats:
        """Extract all stats for the demographics module

        Returns:
            DemographicsStats: Demographics statistics object
        """
        # If we don't already have a patient cohort, extract one
        if self._patient_cohort is None:
            self.extract_patient_cohort(
                include_tracing=include_tracing,
                limit_to_ukrdc=limit_to_ukrdc,
                ukrr_expanded=ukrr_expanded,
            )

        if self._patient_cohort is None:
            raise NoCohortError(
                f"No patient cohort has been extracted. Facility {self.facility} may not have a UKRDC feed."
            )

        pop_size = len(self._patient_cohort[["ukrdcid"]].drop_duplicates())

        # Build output object
        return DemographicsStats(
            metadata=DemographicsMetadata(population=pop_size),
            ethnic_group=self._calculate_ethnic_group_code(),
            gender=self._calculate_gender(),
            age=self._calculate_age(),
        )

    def generate_demographics_report(self, include_ni: bool = False) -> CohortReport:
        pop, report = self.produce_report(
            output_columns=["pid", "ukrdcid", "gender", "ethnicity", "agerange"],
            include_ni=include_ni,
        )

        desc = "Report on demographics for patients with a treatment registered in the ukrdc"

        return CohortReport(
            description=desc, cohort="UKRR Extract", population=pop, table=report
        )
