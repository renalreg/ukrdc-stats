"""
Patient cohort dialysis stats calculator
"""

import warnings

import datetime as dt


from typing import Optional, Tuple, List, Dict

import pandas as pd
from sqlalchemy import and_, func, or_, select, cast, case, exists
from sqlalchemy.orm import Session, aliased
from sqlalchemy.types import Float
from ukrdc_sqla.ukrdc import (
    DialysisSession,
    Patient,
    PatientRecord,
    Treatment,
    ModalityCodes,
)
from sqlalchemy.dialects.postgresql import distinct_on

from ukrdc_stats.calculators.abc import AbstractFacilityStatsCalculator
from ukrdc_stats.utils import _get_satellite_list, aggregate_data
from ukrdc_stats.exceptions import NoCohortError
from pydantic import Field


from ukrdc_stats.descriptions import dialysis_descriptions
from ukrdc_stats.models.generic_2d import (
    AxisLabels2d,
    Labelled2d,
    Labelled2dData,
    Labelled2dMetadata,
    BaseTable,
)

from ukrdc_stats.models.base import JSONModel
from ukrdc_stats.models.reports import CohortReport


class KRTMetadata(JSONModel):
    population: Optional[int] = Field(
        None,
        description="Number of patients in the cohort for dialysis stats calculation",
    )
    from_time: dt.datetime = Field(
        ..., description="Start time of dialysis stats calculations"
    )
    to_time: dt.datetime = Field(
        ..., description="End time of dialysis stats calculations"
    )


class KRTStats(JSONModel):
    """
    Container class for all the dialysis stats
    """

    incident_krt: Labelled2d = Field(
        ...,
        description="statistical breakdown of therapy types for incident patients in cohort",
    )
    prevalent_krt: Labelled2d = Field(
        ...,
        description="statistical breakdown of therapy types for prevalent patients in cohort",
    )
    incentre_dialysis_frequency: Labelled2d = Field(
        ...,
        description="per week frequency of dialysis for all in-centre dialysis patients",
    )
    incentre_time_dialysed: Labelled2d = Field(
        ...,
        description="per week time dialysed for all in-centre dialysis patients",
    )
    incident_initial_access: Labelled2d = Field(
        ...,
        description="vascular access of incident dialysis patients on their first session",
    )
    prevalent_most_recent_access: Labelled2d = Field(
        ...,
        description="vascular access of prevalent dialysis patients on their most recent session",
    )
    metadata: KRTMetadata


class UnitLevelKRTStats(JSONModel):
    all: KRTStats
    units: Dict[str, KRTStats]


def calculate_therapy_types(
    patient_cohort: pd.DataFrame,
) -> Tuple[List[str], List[int]]:
    """
    Breakdown of dialysis patients on home and in-centre therapies.
    The information is returned using pydantic classes designed to handle
    networks (this is essentially what a sankey plot is).

    Args:
        patient_cohort: DataFrame containing patient data.

    Returns:
        Tuple of two lists:
        - labels: A list of strings describing the type of therapy.
        - patients: A list of counts of patients for each type of therapy.
    """

    # Define mappings for 'qbl05' column
    mappings = {"HOSP": "In-centre", "SATL": "In-centre", "HOME": "Home"}

    # Update 'qbl05' based on conditions
    patient_cohort.loc[
        patient_cohort.registry_code_type.isin(["PD", "TX"]), "qbl05"
    ] = ""
    patient_cohort.loc[
        (patient_cohort.registry_code_type == "HD")
        & (patient_cohort.qbl05.isna() | (patient_cohort.qbl05 == "")),
        "qbl05",
    ] = "Unknown/Incomplete"
    patient_cohort.loc[:, "qbl05"] = patient_cohort["qbl05"].replace(mappings)

    aggregate_patients = aggregate_data(
        patient_cohort,
        groupby_attributes=["registry_code_type", "qbl05"],
        deduplicate=True,
    ).sort_values("registry_code_type")

    # Create labels and patients lists
    labels = [
        f"{row.registry_code_type} {row.qbl05}".strip()
        for _, row in aggregate_patients.iterrows()
    ]
    patients = aggregate_patients["value"].tolist()

    return labels, patients


def adjust_next_fromtime(group: pd.DataFrame, **kwargs):
    """
    Utility function to adjust the next_fromtime in the case where there are
    overlaps in the treatment records.

    This function will blank the next_fromtime of all bar the last record in an
    overlapping group. Overlapping group ends when there is a gap not covered
    by treatment. The logic here is slightly fiddly.

    Args:
        group (_type_): _description_

    Returns:
        _type_: _description_
    """
    # skip any single record group
    group = group.reset_index(drop=True)
    if len(group) > 1:
        overlapping = False
        for i in range(len(group) - 1):
            if overlapping:
                ind_final = i
                to_time = group.at[i, "totime"]
                if to_time > max_to_time:  # noqa f841
                    max_to_time = group.at[i, "totime"]

                # overlap group ends where maximum to time in group is less
                # than the next from time.
                next_fromtime = group.at[i, "next_fromtime"]
                if max_to_time <= next_fromtime:
                    overlapping = False

                    # step 2: blank all next_fromtime in overlapping group
                    group.loc[ind_first:ind_final, "next_fromtime"] = pd.NaT  # noqa f821

                    # step 3: select record with maximum totime and add
                    # next_fromtime back in
                    overlap_slice = group[ind_first : ind_final + 1]  # noqa f821
                    max_totime_idx = overlap_slice["totime"].idxmax()
                    group.loc[max_totime_idx, "next_fromtime"] = next_fromtime

            else:
                # step 1: detect any overlapping records in group
                # locate the first record that satisfies the condition
                if group.at[i, "next_fromtime"] < group.at[i, "totime"]:
                    # create indices to track group
                    overlapping = True
                    ind_first = i  # noqa f841
                    ind_final = i
                    max_to_time = group.at[i, "totime"]

    return group


class KRTStatsCalculator(AbstractFacilityStatsCalculator):
    """Class to calculate basic statistics associated with the renal
    replacement therapies for renal facility in a given time window."""

    def __init__(
        self,
        session: Session,
        facility: str,
        date: dt.datetime = None,
        from_time: dt.datetime = None,
        to_time: dt.datetime = None,
    ):
        if to_time and date:
            date = to_time
        elif date:
            to_time = date
        else:
            date = to_time

        super().__init__(session, facility, date)

        if to_time > dt.datetime.now() - dt.timedelta(days=90):
            warnings.warn(
                "Stats calculated for times within the last 90 days may have their accuracy reduced",
            )

        self.recovery_window = 90  # days around the window to look at treatments

        if not from_time:
            from_time = to_time - dt.timedelta(days=365)

        # Create a precisely 2 element time window tuple
        self.time_window: Tuple[dt.datetime, dt.datetime] = (from_time, to_time)

        # defines encoding of KRT treatment types
        self.registry_code_types: List[str] = ["HD", "PD", "TX"]
        self.home_therapy_code_types: List[str] = ["HOSP", "SATL", "INCENTRE"]
        self.satellite_units = _get_satellite_list(facility, session)

    def _extract_patient_cohort(self, limit_to_ukrdc: Optional[bool] = True):
        """
        Extract a complete patient cohort dataframe. This is calculated fresh
        each time but we would probably want to implement some caching here.
        """
        self._patient_cohort = self._extract_incident_prevalent(
            self._extract_base_patient_cohort(limit_to_ukrdc=limit_to_ukrdc)
        )

    def _extract_base_patient_cohort(
        self,
        limit_to_ukrdc: Optional[bool] = True,
    ) -> pd.DataFrame:
        """Core query from which the other stats is derived. All patients at a
        renal facility with treatments up to and including 90 days post and
        prior to the time window will be included into the base cohort. Query
        will also flag any patients which had a historical ckd diagnosis or
        transplant. This should be rigorously back tested with real data and
        any changes should be considered breaking changes only to be done in a
        major release.

        Returns:
            pd.DataFrame: Patient cohort dataframe
        """

        minimum_transplant_length = 7

        ChronicTreatment = aliased(Treatment)  # pylint: disable=C0103
        ChronicModality = aliased(ModalityCodes)  # pylint: disable=C0103
        HistoricTransplantTreatment = aliased(Treatment)  # pylint: disable=C0103
        TransplantModality = aliased(ModalityCodes)  # pylint: disable=C0103
        SubPatientRecord = aliased(PatientRecord)

        # Select ukrdcids of patients treated at facility
        ukrdc_sub = select(PatientRecord.ukrdcid).where(
            PatientRecord.sendingfacility == self.facility
        )

        chronic_check = exists().where(
            ChronicTreatment.pid == SubPatientRecord.pid,
            SubPatientRecord.ukrdcid == PatientRecord.ukrdcid,
            ChronicTreatment.fromtime
            < self.time_window[1],  # Check if within time window
            ChronicTreatment.admitreasoncode
            == ChronicModality.registry_code,  # Match chronic modality code
            ChronicModality.registry_code_type == "CK",
        )

        tx_check = exists().where(
            HistoricTransplantTreatment.pid == SubPatientRecord.pid,
            SubPatientRecord.ukrdcid == PatientRecord.ukrdcid,
            HistoricTransplantTreatment.fromtime
            < self.time_window[0],  # Before start of time window
            HistoricTransplantTreatment.totime - HistoricTransplantTreatment.fromtime
            > dt.timedelta(days=minimum_transplant_length),  # Successful transplant
            HistoricTransplantTreatment.admitreasoncode
            == TransplantModality.registry_code,
            TransplantModality.registry_code_type == "TX",
        )

        if limit_to_ukrdc:
            ukrdc_sub.where(PatientRecord.sendingextract == "UKRDC")
            chronic_check.where(SubPatientRecord.sendingextract == "UKRDC")
            tx_check.where(SubPatientRecord.sendingextract == "UKRDC")

        query = (
            select(
                PatientRecord.pid,
                PatientRecord.ukrdcid,
                PatientRecord.sendingfacility,
                Treatment.healthcarefacilitycode,
                Treatment.admitreasoncode,
                Treatment.admitreasoncodestd,
                Treatment.admissionsourcecode,
                Treatment.admissionsourcecodestd,
                Treatment.qbl05,
                Treatment.hdp04,
                Treatment.dischargereasoncode,
                Treatment.dischargereasoncodestd,
                Treatment.dischargelocationcode,
                Treatment.dischargelocationcodestd,
                ModalityCodes.registry_code_type,
                Patient.deathtime,
                Treatment.fromtime,
                Treatment.totime,
                # Correlated subquery for chronic treatment check
                case(
                    (
                        chronic_check,
                        True,
                    ),
                    else_=False,
                ).label("is_chronic"),
                # Correlated subquery for historical transplant check
                case(
                    (
                        tx_check,
                        True,
                    ),
                    else_=False,
                ).label("historic_tx"),
            )
            .select_from(PatientRecord)
            .join(Patient, Patient.pid == PatientRecord.pid)
            .join(Treatment, Treatment.pid == PatientRecord.pid)
            .join(
                ModalityCodes, ModalityCodes.registry_code == Treatment.admitreasoncode
            )
            .where(
                ModalityCodes.registry_code_type.in_(self.registry_code_types),
                or_(
                    Treatment.totime > self.time_window[0] - dt.timedelta(days=90),
                    Treatment.totime.is_(None),
                ),
                or_(
                    Patient.deathtime > self.time_window[0], Patient.deathtime.is_(None)
                ),
                PatientRecord.ukrdcid.in_(ukrdc_sub),
            )
        )

        # apply cutoff. Implicitly if calculating for date more recent than
        # cutoff we allow all fromtimes
        if dt.datetime.now() - self.time_window[1] > dt.timedelta(
            days=self.recovery_window
        ):
            query = query.where(
                Treatment.fromtime
                < self.time_window[1] + dt.timedelta(days=self.recovery_window)
            )

        if limit_to_ukrdc:
            query = query.where(PatientRecord.sendingextract == "UKRDC")

        # Execute query and explicitly specify the datatypes of columns
        base_cohort = pd.read_sql(
            query,
            self.session.bind,
            dtype={
                "pid": "string",
                "ukrdcid": "string",
                "sendingfacility": "string",
                "healthcarefacilitycode": "string",
                "admitreasoncode": "string",
                "admitreasoncodestd": "string",
                "admissionsourcecode": "string",
                "admissionsourcecodestd": "string",
                "qbl05": "string",
                "hdp04": "string",
                "dischargereasoncode": "string",
                "dischargereasoncodestd": "string",
                "dischargelocationcode": "string",
                "dischargelocationcodestd": "string",
                "registry_code_type": "string",
                "deathtime": "datetime64[ns]",
                "fromtime": "datetime64[ns]",
                "totime": "datetime64[ns]",
                "is_chronic": "bool",
                "historic_tx": "bool",
            },
        )

        if base_cohort.empty:
            raise NoCohortError(
                f"No patient cohort has been extracted. Facility {self.facility} may not have a UKRDC feed."
            )

        # pandas by default tries to be helpful and create compound keys
        # this is more overly helpful so we drop them
        base_cohort = base_cohort.reset_index(drop=True)

        return base_cohort

    def _chain_treatments(self, raw_patients: pd.DataFrame):
        """We append columns to the dataframe to allow recovery based
        calculations to be made.
        """

        raw_patients = raw_patients.sort_values(
            by=["ukrdcid", "fromtime", "sendingfacility"]
        )

        # append the start of the next treatment to each record
        raw_patients["next_fromtime"] = raw_patients.groupby("ukrdcid")[
            "fromtime"
        ].shift(-1)

        raw_patients = raw_patients.groupby("ukrdcid", as_index=False)[
            raw_patients.columns
        ].apply(adjust_next_fromtime, include_groups=False)

        return raw_patients

    def _exclude_records(self, base_cohort: pd.DataFrame):
        """This implements any conditions the might cause a patient to be removed from
        the cohort. For example anyone who is in a 90 recovery period which spans the
        end of the time window should be excluded. Any patient with treatment modality
        code which implies CKD that dies before the end of the window will also be
        excluded.
        """

        # recovery window
        recoveries = (
            base_cohort["next_fromtime"] - base_cohort["totime"]
        ) > dt.timedelta(days=90)
        patient_recoveries = base_cohort[recoveries][
            ["ukrdcid", "next_fromtime", "totime"]
        ]

        index_to_remove = []
        for _, row in patient_recoveries.iterrows():
            if row["totime"] >= self.time_window[1]:
                # patient has made a recovery remove future records
                index_to_remove.extend(
                    base_cohort[
                        (base_cohort["ukrdcid"] == row["ukrdcid"])
                        & (base_cohort["fromtime"] > row["totime"])
                    ].index
                )
            else:
                if row["next_fromtime"] > self.time_window[1]:
                    # patient was recovered at end of window remove completely
                    index_to_remove.extend(
                        base_cohort[base_cohort["ukrdcid"] == row["ukrdcid"]].index
                    )

                else:
                    # patient coming out of recovery, remove record and all prior
                    index_to_remove.extend(
                        base_cohort[
                            (base_cohort["ukrdcid"] == row["ukrdcid"])
                            & (base_cohort["totime"] <= row["totime"])
                        ].index
                    )

        base_cohort = base_cohort.drop(index=index_to_remove)

        return base_cohort

    def _add_helper_columns(self, base_cohort: pd.DataFrame):
        """Function to postprocess data and add column to help with the
        calculation of incident and prevalent cohorts.

        Args:
            base_cohort (pd.DataFrame): Raw patient cohort generated by
            directly querying the database into pandas.

        Returns:
            _type_: _description_
        """

        # clean open records by replace na with infinities
        deathtime_default = self.time_window[1] + dt.timedelta(days=5000)
        totime_default = self.time_window[1] + dt.timedelta(days=5000)

        base_cohort["deathtime"] = base_cohort["deathtime"].fillna(deathtime_default)
        base_cohort["totime"] = base_cohort["totime"].fillna(totime_default)

        # run function to link each treatment to the one that follows
        base_cohort = self._chain_treatments(base_cohort)

        # Exclude "acute" patients and records post or prior to recoveries
        base_cohort = self._exclude_records(base_cohort)

        # identify the records with the most recent from time for each ukrdcid
        most_recent = base_cohort[
            base_cohort["fromtime"] < self.time_window[1]
        ].reset_index(drop=True)
        most_recent = most_recent.groupby("ukrdcid", as_index=False)["fromtime"].max()

        # Merge with the original cohort to identify the most recent treatments
        base_cohort = base_cohort.merge(
            most_recent, on=["ukrdcid"], how="left", suffixes=("", "_max")
        )

        # Mark beginning and end of treatment timeline
        base_cohort["timeline_start"] = base_cohort.groupby("ukrdcid", as_index=False)[
            "fromtime"
        ].transform("min")
        base_cohort["timeline_stop"] = base_cohort.groupby("ukrdcid", as_index=False)[
            "totime"
        ].transform("max")

        # Mark first and most recent treatments
        base_cohort["first_treatment"] = (
            base_cohort["timeline_start"] == base_cohort["fromtime"]
        )
        base_cohort["most_recent"] = False
        base_cohort.loc[
            base_cohort.groupby("ukrdcid")
            .apply(
                lambda x: x["totime"].idxmax()
                if x["totime"].notna().any()
                else x["fromtime"].idxmax(),
                include_groups=False
            )
            .values,
            "most_recent",
        ] = True

        # Calculate length of timeline
        # Null value here is >90days
        # TODO: this needs revisiting maybe a safer way of doing it is to
        # select whichever is larger now() - timeline_start or 91 days
        # this would be non deterministic
        #base_cohort["timeline_length"] = (
        #    base_cohort["timeline_stop"] - base_cohort["timeline_start"]
        #).fillna(dt.timedelta(days=91))
        
        base_cohort["timeline_length"] = (
            base_cohort["timeline_stop"] - base_cohort["timeline_start"]
        )

        # Ensure deathtime exists for mocked/test data that may omit the column
        if "deathtime" not in base_cohort.columns:
            base_cohort["deathtime"] = pd.NaT

        base_cohort["life_length"] = (
            base_cohort["deathtime"] - base_cohort["timeline_start"]
        )

        #base_cohort["life_length"] = (
        #    base_cohort["deathtime"] - base_cohort["timeline_start"]
        #).fillna(dt.timedelta(days=91))

        return base_cohort

    def _extract_incident_prevalent(self, base_cohort: pd.DataFrame) -> pd.DataFrame:
        """
        The function calculates the incident and prevalent cohorts as precisely
        as possible to the definition used in the annual report. However lack
        of full coverage means that transfer in patients will appear as
        incident patients. These will like lead to the incident cohorts to be
        an over estimate and prevalent cohorts to be an underestimate.

        Args:
            base_cohort (pd.DataFrame): Base cohort from output of _extract_base_patient_cohort
        Returns:
            pd.DataFrame: Patient cohort dataframe
        """

        # Generate some helper columns to label patients in helpful ways
        base_cohort = self._add_helper_columns(base_cohort)

        # Without full coverage we can do anything super accurate with transfer
        # out. However we will treat certain dischargereason codes as idicating
        # continued treatment.
        discharge_reasons = []  # = ["38"]?
        discharge_locations = ["ABROAD"]
        transfered_patients = base_cohort[
            (
                base_cohort["dischargelocationcode"].isin(discharge_locations)
                | base_cohort["dischargereasoncode"].isin(discharge_reasons)
            )
            & base_cohort.most_recent
        ].ukrdcid.drop_duplicates()
        transfered_out = base_cohort.ukrdcid.isin(transfered_patients)

        # Crash landed patients are defined:
        # - no chronic treatment records or tx
        # - remains on KRT for more than 90 days or transfered out
        # - survives for more than 90 days
        is_crash_landing = (
            (~base_cohort["is_chronic"] & ~base_cohort["historic_tx"])
            & (
                (base_cohort["timeline_length"] > dt.timedelta(days=90))
                | transfered_out
            )
            & (base_cohort["life_length"] > dt.timedelta(days=90))
        )

        # Patients with a previous record of transplant or ckd are considered
        # planned for KRT. These patients must stay on KRT for more than 90
        # days or die to be counted as incident.
        planned_ckd = (base_cohort["is_chronic"] | base_cohort["historic_tx"]) & (
            (base_cohort["timeline_length"] > dt.timedelta(days=90))
            | base_cohort["timeline_length"].isna()
            | transfered_out
        ) | (base_cohort["life_length"] < dt.timedelta(days=90))

        # Do we exclude patients which have had a historical transplant?
        # Also check treatments which turn up from different units.
        base_cohort["incident"] = (
            (planned_ckd | is_crash_landing)
            & (base_cohort["timeline_start"] > self.time_window[0])
            & (base_cohort["timeline_start"] <= self.time_window[1])
        )

        # Prevalence point defined at the end of the window patients are
        # counted as prevalent if their treatment timeline spans the end of the
        # window and is greater than 90 days.
        # base_cohort["prevalent"] = (
        #    (base_cohort["timeline_start"] <= self.time_window[1])
        #    & (
        #        (base_cohort["timeline_stop"] > self.time_window[1])
        #        | base_cohort["timeline_stop"].isna()
        #    )
        #    & (base_cohort["timeline_length"] > dt.timedelta(days=90))
        # )

        # TODO: reintroduce the concept of being on treatment for more than 90
        # days
        prevalent_ids = base_cohort[
            (base_cohort["fromtime"] < self.time_window[1])
            & (
                (base_cohort["totime"] > self.time_window[1])
                | base_cohort["totime"].isna()
            )
        ].ukrdcid.drop_duplicates()
        base_cohort["prevalent"] = base_cohort.ukrdcid.isin(prevalent_ids)

        return base_cohort

    def _query_dialysis_sessions(
        self, patient_list: List[str], start: dt.datetime, stop: dt.datetime
    ) -> pd.DataFrame:
        # Calculate start of each week interval
        week_start = func.date_trunc("week", DialysisSession.procedure_time)

        dialysis_snomed = ["302497006", "233581009", "233586004"]

        query = (
            select(
                PatientRecord.pid,
                PatientRecord.ukrdcid,
                week_start.label("weekstart"),
                func.count(DialysisSession.procedure_type_code).label("hdsessionno"),
                func.sum(cast(DialysisSession.qhd31, Float)).label("totaltimedialised"),
            )
            .join(DialysisSession, DialysisSession.pid == PatientRecord.pid)
            .where(
                and_(
                    PatientRecord.pid.in_(patient_list),
                    DialysisSession.procedure_type_code.in_(
                        dialysis_snomed
                    ),  # filter for hd
                    DialysisSession.procedure_time > start,
                    DialysisSession.procedure_time < stop,
                )
            )
            .group_by(PatientRecord.pid, week_start)
        )

        session_data = pd.DataFrame(self.session.execute(query)).drop_duplicates()

        return session_data

    def _calculate_dialysis_frequency(self, subunit: str = "all") -> Labelled2d:
        """_summary_

        Args:
            subunit (str, optional): _description_. Defaults to "all".

        Returns:
            Labelled2d: _description_
        """

        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        patient_list = self._patient_cohort[
            (self._patient_cohort.registry_code_type == "HD")
            & (self._patient_cohort.qbl05.isin(["HOSP", "SATL", "In-centre"]))
            & self._patient_cohort.prevalent
        ]

        if subunit == "all":
            patient_list = patient_list[
                (patient_list.healthcarefacilitycode == self.facility)
                | (patient_list.healthcarefacilitycode.isin(self.satellite_units))
                | patient_list.healthcarefacilitycode.isna()
            ].pid.drop_duplicates()
        else:
            patient_list = patient_list[
                patient_list.healthcarefacilitycode == subunit
            ].pid.drop_duplicates()

        dialysis_frequency_meta = Labelled2dMetadata(
            title="Median Haemodialysis Frequency",
            summary="Median frequency of incentre haemodialysis per week.",
            description=dialysis_descriptions["INCENTRE_DIALYSIS_FREQ"],
            axis_titles=AxisLabels2d(
                x="Frequency (days per week)", y="No. of Patients"
            ),
        )

        dialysis_time_meta = Labelled2dMetadata(
            title="Median Haemodialysis Time",
            summary="Median time of incentre haemodialysis per week.",
            description=dialysis_descriptions["INCENTRE_DIALYSIS_TIME"],
            axis_titles=AxisLabels2d(x="Time (hours per week)", y="No. of Patients"),
        )

        data_frequency = Labelled2dData(x=[], y=[])
        data_timedialised = Labelled2dData(x=[], y=[])
        if not patient_list.empty:
            # get number of dialysis sessions per patient and the date of the first and last one
            session_data = self._query_dialysis_sessions(
                patient_list, self.time_window[0], self.time_window[1]
            )
            if not session_data.empty:
                # drop any rows where hdsessionno is 0
                session_data = session_data[session_data["hdsessionno"] > 0]

                # Calculate the median of hdsessionno and timedialysed per ukrdcid
                median_data = (
                    session_data.groupby("ukrdcid")
                    .agg(
                        median_hdsessionno=pd.NamedAgg(
                            column="hdsessionno", aggfunc="median"
                        ),
                        median_timedialysed=pd.NamedAgg(
                            column="totaltimedialised", aggfunc="median"
                        ),
                    )
                    .reset_index()
                )

                freq_bins = [0.0, 1.0, 2.0, 3.0, 7.0]
                freq_labels = ["1", "2", "3", ">3"]

                frequency_hist = pd.cut(
                    median_data.median_hdsessionno, bins=freq_bins, labels=freq_labels
                ).value_counts(sort=False)

                time_bins = [0, 200, 400, 600, 800, 1000, 1000000]
                time_labels = [
                    "<200",
                    "201-400",
                    "401-600",
                    "601-800",
                    "801-1000",
                    ">1001",
                ]

                # create custom bins
                time_hist = pd.cut(
                    median_data.median_timedialysed, bins=time_bins, labels=time_labels
                ).value_counts(sort=False)

                # Update data_frequency with histogram data
                data_frequency.x = frequency_hist.index.tolist()
                data_frequency.y = frequency_hist.tolist()

                # Update data_timedialised with histogram data
                data_timedialised.x = time_hist.index.tolist()
                data_timedialised.y = time_hist.tolist()

        time_dialysis = Labelled2d(data=data_timedialised, metadata=dialysis_time_meta)
        frequency_dialysis = Labelled2d(
            data=data_frequency, metadata=dialysis_frequency_meta
        )

        return frequency_dialysis, time_dialysis

    def _calculate_median_dialysis_frequency(self, subunit: str = "all") -> Labelled2d:
        """Placeholder incase we revisit the idea of calculating the median
        dialysis frequency which would be more in line with what is calculated
        for the annual report.
        """
        del subunit
        return

    def _query_first_vascular_access(self, patient_list: pd.Series) -> pd.DataFrame:
        """
        SQL query to retreive the first vascular access used within a grace
        period of the first recorded dialysis session for a list of patients.

        Args:
            patient_list (pd.Series): List of pids defining a cohort.

        Returns:
            pd.DataFrame:  pid, accesstype, proceduretime
        """

        grace_period = 14
        time_windows = (
            select(
                DialysisSession.pid,
                DialysisSession.procedure_time.label("start_time"),
                (
                    DialysisSession.procedure_time + dt.timedelta(days=grace_period)
                ).label("end_time"),
            )
            .where(DialysisSession.pid.in_(patient_list))
            .distinct(DialysisSession.pid)
            .order_by(DialysisSession.pid, DialysisSession.procedure_time.asc())
        ).subquery()

        d = aliased(DialysisSession)
        vascular_access_query = (
            select(d.pid, d.qhd20, d.procedure_time)
            .join(
                time_windows,
                and_(
                    d.pid == time_windows.c.pid,
                    d.procedure_time.between(
                        time_windows.c.start_time, time_windows.c.end_time
                    ),
                ),
            )
            .where(d.qhd20.isnot(None))
            .ext(distinct_on(DialysisSession.pid))
            .order_by(d.pid, d.procedure_time.asc())
        )

        results = pd.DataFrame(self.session.execute(vascular_access_query))
        results = results.rename(columns={"qhd20": "accesstype"})

        return results

    def _query_most_recent_vascular_access(
        self, patient_list: pd.Series
    ) -> pd.DataFrame:
        """
        SQL query to retreive the most recent vascular access used within a grace
        period of the end date specified in the calculator.

        Args:
            patient_list (pd.Series): List of pids defining a cohort.

        Returns:
            pd.DataFrame: DataFrame with columns pid, accesstype, proceduretime
        """

        vascular_access_query = (
            select(
                DialysisSession.pid,
                DialysisSession.qhd20,
                DialysisSession.procedure_time,
            )
            .where(
                DialysisSession.pid.in_(patient_list),
                DialysisSession.procedure_time <= self.time_window[1],
                # DialysisSession.procedure_time >= self.time_window[1] - dt.timedelta(days=grace_period),
                DialysisSession.qhd20.isnot(None),
            )
            .distinct(DialysisSession.pid)
            .order_by(DialysisSession.pid, DialysisSession.procedure_time.desc())
        )

        results = pd.DataFrame(self.session.execute(vascular_access_query))
        results = results.rename(columns={"qhd20": "accesstype"})

        return results

    def _query_vascular_access(
        self, patient_list: pd.Series, is_first=True
    ) -> pd.DataFrame:
        """
        Function to return query either the first or the most recent vascular
        access for a list of patients. Patient list is batched to prevent the
        query size overflowing the maximum allowed.

        Args:
            patient_list (pd.Series): List of pids defining a cohort.
            is_first (bool, optional): Whether to return the first or most recent vascular access. Defaults to True.

        Returns:
            pd.DataFrame: DataFrame with columns pid, qhd20, proceduretime
        """
        CHUNK_SIZE = 100
        all_results = []

        pids = patient_list.tolist()

        for i in range(0, len(pids), CHUNK_SIZE):
            chunk_pids = pids[i : i + CHUNK_SIZE]

            if is_first:
                chunk_results = self._query_first_vascular_access(chunk_pids)
            else:
                chunk_results = self._query_most_recent_vascular_access(chunk_pids)

            if not chunk_results.empty:
                all_results.append(chunk_results)

        if all_results:
            vascular_access_data = pd.concat(all_results, ignore_index=True)
        else:
            vascular_access_data = pd.DataFrame(
                columns=["pid", "accesstype", "procedure_time"]
            )

        return vascular_access_data

    def _calculate_access_incident(self, subunit: str = "all") -> Labelled2d:
        """Displays the vascular access of incident patients on their first dialysis session
        Args:
            subunit (str, optional): Satellite unit. Defaults to "all".
        Raises:
            NoCohortError: e.g. if extract_patient_cohort has not been run
        Returns:
            Labelled2d: Number of incident patients with each type of access
        """

        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        # select pids of incident hd patients
        if subunit == "all":
            self._incident_access = self._patient_cohort[
                self._patient_cohort.incident
                & (
                    (self._patient_cohort.healthcarefacilitycode == self.facility)
                    | (
                        self._patient_cohort.healthcarefacilitycode.isin(
                            self.satellite_units
                        )
                    )
                    | self._patient_cohort.healthcarefacilitycode.isna()
                )
                & self._patient_cohort.first_treatment
                & (self._patient_cohort.registry_code_type == "HD")
            ]
        else:
            self._incident_access = self._patient_cohort[
                self._patient_cohort.incident
                & (self._patient_cohort.healthcarefacilitycode == subunit)
                & (self._patient_cohort.registry_code_type == "HD")
                & self._patient_cohort.first_treatment
            ]

        # function runs queries against the vascular access table and map to patients
        initial_access_data = self._query_vascular_access(
            self._incident_access.pid.drop_duplicates()
        )
        self._incident_access = self._incident_access.merge(
            initial_access_data[["pid", "accesstype"]],
            on="pid",
            how="left",
        )

        self._incident_access.loc[
            self._incident_access.accesstype.isna(), "accesstype"
        ] = "Unknown/Incomplete"

        # aggregate cohort
        aggregate_patients = aggregate_data(
            self._incident_access, groupby_attributes=["accesstype"]
        )

        x_data = list(aggregate_patients.accesstype)
        y_data = list(aggregate_patients.value)

        return Labelled2d(
            metadata=Labelled2dMetadata(
                title="Vascular Access on First HD Session",
                summary="Vascular access for incident patients registered on their first dialysis session.",
                description=dialysis_descriptions["INCIDENT_INITIAL_ACCESS"],
                axis_titles=AxisLabels2d(x="Line Type", y="No. of Patients"),
                population_size=sum(y_data),
            ),
            data=Labelled2dData(x=x_data, y=y_data),
        )

    def _calculate_access_prevalent(self, subunit: str = "all") -> Labelled2d:
        """Calculates the prevalent vascular access using the most recent access data.

        Args:
            subunit (str, optional): Satellite unit. Defaults to "all".

        Raises:
            NoCohortError: If no patient cohort has been extracted.

        Returns:
            Labelled2d: Number of prevalent patients with each type of access.
        """

        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        # Filter by subunit
        if subunit == "all":
            patient_list = self._patient_cohort[
                self._patient_cohort.prevalent
            ].pid.drop_duplicates()
        else:
            patient_list = self._patient_cohort[
                self._patient_cohort.prevalent
                & (self._patient_cohort.healthcarefacilitycode == subunit)
            ].pid.drop_duplicates()

        # Query the most recent vascular access
        recent_access_data = self._query_vascular_access(patient_list, is_first=False)
        self._prevalent_access = self._patient_cohort[
            self._patient_cohort.prevalent
        ].merge(
            recent_access_data[["pid", "accesstype"]],
            on="pid",
            how="left",
        )

        self._prevalent_access.loc[
            self._prevalent_access.accesstype.isna(), "accesstype"
        ] = "Unknown/Incomplete"

        # Aggregate cohort
        aggregate_patients = aggregate_data(
            self._prevalent_access, groupby_attributes=["accesstype"]
        )
        x_data = list(aggregate_patients.accesstype)
        y_data = list(aggregate_patients.value)

        return Labelled2d(
            metadata=Labelled2dMetadata(
                title="Most Recent Vascular Access",
                summary="Vascular access for prevalent patients registered on their most recent dialysis session.",
                description=dialysis_descriptions["PREVALENT_MOST_RECENT_ACCESS"],
                axis_titles=AxisLabels2d(x="Line Type", y="No. of Patients"),
                population_size=0,
            ),
            data=Labelled2dData(x=x_data, y=y_data),
        )

    def _calculate_therapies_incident_patients(
        self, subunit: str = "all"
    ) -> Labelled2d:
        """Wrapper for calculate_therapy_types to calculate therapy types for an incident cohort
        Args:
            subunit (str, optional): Satellite unit. Defaults to "all".
        Raises:
            NoCohortError: _description_
        Returns:
            Labelled2d: Types of dialysis for incident patient cohort
        """

        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        # TODO: add in something to assign patients to where they were first seen
        if subunit == "all":
            incident_cohort = self._patient_cohort[
                self._patient_cohort.incident
                & self._patient_cohort.first_treatment
                & (
                    (self._patient_cohort.healthcarefacilitycode == self.facility)
                    | (
                        self._patient_cohort.healthcarefacilitycode.isin(
                            self.satellite_units
                        )
                    )
                    | self._patient_cohort.healthcarefacilitycode.isna()
                )
            ]
        else:
            incident_cohort = self._patient_cohort[
                self._patient_cohort.incident
                & (self._patient_cohort.healthcarefacilitycode == subunit)
                & self._patient_cohort.first_treatment
            ]

        incident_labels, incident_no = calculate_therapy_types(incident_cohort)

        return Labelled2d(
            metadata=Labelled2dMetadata(
                title="Incident KRT Modalities",
                summary="Breakdown of incident patients on PD and HD, and by home therapies and in-centre therapies.",
                description=dialysis_descriptions["INCIDENT_KRT_COHORT"],
                population_size=sum(incident_no),
            ),
            data=Labelled2dData(x=incident_labels, y=incident_no),
        )

    def _calculate_therapies_prevalent_patients(self, subunit: str = "all"):
        """Wrapper for calculate_therapy_types to calculate therapy types for an prevalent cohort
        Args:
            subunit (str, optional): Satellite unit. Defaults to "all".
        Raises:
            NoCohortError: _description_
        Returns:
            Labelled2d: Types of dialysis for prevalent patient cohort
        """

        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        # filter patient cohort to get the first treatment type of each patient
        if subunit == "all":
            prevalent_cohort = self._patient_cohort[
                self._patient_cohort.prevalent
                & self._patient_cohort.first_treatment
                & (
                    (self._patient_cohort.healthcarefacilitycode == self.facility)
                    | (
                        self._patient_cohort.healthcarefacilitycode.isin(
                            self.satellite_units
                        )
                    )
                    | self._patient_cohort.healthcarefacilitycode.isna()
                )
            ]

        else:
            prevalent_cohort = self._patient_cohort[
                self._patient_cohort.prevalent
                & self._patient_cohort.most_recent
                # & self._patient_cohort.first_treatment
                & (self._patient_cohort.healthcarefacilitycode == subunit)
            ]

        prevalent_labels, prevalent_no = calculate_therapy_types(prevalent_cohort)

        return Labelled2d(
            metadata=Labelled2dMetadata(
                title="Prevalent KRT Modalities",
                summary="Breakdown of prevalent patients by PD and HD, and by home therapies and in-centre therapies.",
                description=dialysis_descriptions["PREVALENT_KRT_COHORT"],
                population_size=sum(prevalent_no),
            ),
            data=Labelled2dData(x=prevalent_labels, y=prevalent_no),
        )

    def extract_satellite_stats(self, unit: str = "all") -> KRTStats:
        """
        Returns:
            KRTStats:
        """

        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        # population size calculated from the sum of the incident and prevalant patients
        all_patients = self._patient_cohort[
            self._patient_cohort.incident | self._patient_cohort.prevalent
        ]
        if not unit == "all":
            all_patients = all_patients[all_patients.healthcarefacilitycode == unit]

        pop_size = len(all_patients.ukrdcid.unique())

        incident_krt = self._calculate_therapies_incident_patients(subunit=unit)
        prevalent_krt = self._calculate_therapies_prevalent_patients(subunit=unit)
        incentre_dialysis_frequency, incentre_time_dialysed = (
            self._calculate_dialysis_frequency(subunit=unit)
        )
        incident_initial_access = self._calculate_access_incident(subunit=unit)
        prevalent_most_recent_access = self._calculate_access_prevalent(subunit=unit)

        return KRTStats(
            metadata=KRTMetadata(
                population=pop_size,
                from_time=self.time_window[0],
                to_time=self.time_window[1],
            ),
            incident_krt=incident_krt,
            prevalent_krt=prevalent_krt,
            incentre_dialysis_frequency=incentre_dialysis_frequency,
            incentre_time_dialysed=incentre_time_dialysed,
            incident_initial_access=incident_initial_access,
            prevalent_most_recent_access=prevalent_most_recent_access,
        )

    def extract_stats(
        self,
        limit_to_ukrdc: Optional[bool] = True,
    ) -> UnitLevelKRTStats:
        """Extract all stats for the dialysis module
        Returns:
            KRTStats: Dialysis statistics object
        """
        # If we don't already have a patient cohort, extract one

        if self._patient_cohort is None:
            self.extract_patient_cohort(
                limit_to_ukrdc=limit_to_ukrdc,
            )

        if self._patient_cohort is None:
            raise NoCohortError("No patient cohort has been extracted")

        if self._patient_cohort.empty:
            raise NoCohortError(
                f"No patients found the cohort. Did you mean to try and extract facility {self.facility}?"
            )

        # calculate stats for all units
        unit_stats: Dict[str, KRTStats] = {}

        # loop over each unit and calculate stats
        for unit in self.satellite_units:
            unit_stats[unit] = self.extract_satellite_stats(unit)

        unit_stats[self.facility] = self.extract_satellite_stats(self.facility)
        return UnitLevelKRTStats(all=self.extract_satellite_stats(), units=unit_stats)

    def generate_cohort_report(
        self, cohort: str, include_ni: bool = False
    ) -> BaseTable:
        """
        Create a report to return the modalities of either incident or
        prevalent patients specified by the calculator.

        Args:
            cohort (str): "incident" or "prevalent"
            include_ni (bool, optional): Whether to include NI patients. Defaults to False.

        Returns:
            BaseTable: A table containing the modalities and incidence/prevalence
            status of patient for the cohort specified by the calculator.
        """

        # check the centre is in the output
        if cohort == "incident":
            pop, report = self.produce_report(
                [
                    "pid",
                    "healthcarefacilitycode",
                    "admitreasoncode",
                    "admitreasoncodestd",
                    "fromtime",
                    "totime",
                    "registry_code_type",
                ],
                [cohort, "first_treatment", f"sendingfacility == '{self.facility}'"],
                include_ni=include_ni,
            )
        elif cohort == "prevalent":
            # This needs reviewing since the proper definition of prevalence should be
            pop, report = self.produce_report(
                [
                    "pid",
                    "healthcarefacilitycode",
                    "admitreasoncode",
                    "admitreasoncodestd",
                    "fromtime",
                    "totime",
                    "registry_code_type",
                ],
                [cohort, "most_recent", f"sendingfacility == '{self.facility}'"],
                include_ni=include_ni,
            )

        desc = f"Report on the treatment modalities of the {cohort} cohort. This report contains a table which includes the most recent treatment modality and the way it's classified by the renal registry along with the ukrdc patient identifier."

        return CohortReport(
            description=desc, cohort=cohort, population=pop, table=report
        )
