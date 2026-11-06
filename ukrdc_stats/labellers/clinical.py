import datetime as dt
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session
from ukrdc_stats.utils.database import get_archive_sessionmaker
from ukrdc_stats.utils.query import pid_ni_map
from ukrdc_stats.labellers.query import (
    query_careplanning,
    query_vascular_access,
    query_dialysis_sessions,
    query_first_seen,
)
from ukrdc_stats.exceptions import MissingColumnError


def _check_ukrdc_extract(cohort: pd.DataFrame) -> None:
    """
    Careplanning labelling relies on the archive database which only holds
    UKRDC extract data, so cohorts built from any other sendingextract
    cannot be labelled.
    """

    if "sendingextract" not in cohort.columns:
        raise MissingColumnError("cohort must contain 'sendingextract' column")

    other_extracts = set(cohort["sendingextract"].unique()) - {"UKRDC"}
    if other_extracts:
        raise NotImplementedError(
            f"sendingextract values {sorted(other_extracts)} are not supported: "
            "careplanning labelling relies on the archive database which only "
            "holds UKRDC data"
        )


def prevalent_careplanning(
    session,
    cohort,
    prevalence_date,
    assessment_type=["TPLTassess", "KRTassess", "PPDassess"],
) -> pd.DataFrame:
    """
    Function labels a cohort of patients with the care planning assessment data
    based on a point of time.

    Args:
        session (_type_): _description_
        cohort (_type_): _description_
        prevalence_date (_type_): _description_
        assessment_type (str, optional): _description_. Defaults to "TPLTassess".

    Raises:
        ValueError: _description_
        ValueError: _description_
        ValueError: _description_

    Returns:
        _type_: _description_
    """

    _check_ukrdc_extract(cohort)

    for at in assessment_type:
        if at not in ["TPLTassess", "KRTassess", "PPDassess"]:
            msg = f""" Assessment type {at} passed which is not in the dataset:
            TPLTassess -> transplant suitability assessment 
            KRTassess -> prefered choice of krt 
            PPDassess -> prefered place of dying
            """
            raise ValueError(msg)

    if "pid" not in cohort.columns:
        raise MissingColumnError("cohort must contain 'pid' column")

    if "sendingfacility" not in cohort.columns:
        raise MissingColumnError("cohort must contain 'sendingfacility' column")

    archive_sessionmaker = get_archive_sessionmaker(session)
    sending_facilities = cohort["sendingfacility"].unique().tolist()

    with archive_sessionmaker() as archive_session:
        careplanning_data = query_careplanning(
            archive_session, sending_facilities, assessment_type, prevalence_date
        )

    # Map patient IDs using pid_ni_map
    pid_map = pid_ni_map(session, sending_facilities)
    careplanning_data = careplanning_data.merge(
        pid_map,
        on=["patientid", "organization", "sendingfacility"],
        how="left",
    )

    careplanning_data = careplanning_data[
        [
            "pid",
            "assessmenttypecode",
            "assessmentstart",
            "assessmentend",
            "assessmentoutcomecode",
        ]
    ].copy()
    careplanning_data["assessmentoutcome"] = (
        careplanning_data["assessmentoutcomecode"]
        .map({"1": "Unsuitable", "2": "In-progress", "3": "Suitable"})
        .fillna("Other")
    )

    # join careplanning to cohort
    cohort = cohort.merge(careplanning_data, on="pid", how="left")
    cohort["assessmentoutcome"] = cohort["assessmentoutcome"].fillna("No assessment")

    return cohort


def pre_start_careplanning(
    session, cohort, assessment_type="TPLTassess"
) -> pd.DataFrame:
    """
    Function labels a cohort of incident patients with the most recent care
    planning assessment prior to each patient's KRT start date (fromtime).

    Args:
        session (_type_): _description_
        cohort (_type_): _description_
        assessment_type (str, optional): _description_. Defaults to "TPLTassess".

    Raises:
        ValueError: _description_
        MissingColumnError: _description_

    Returns:
        _type_: _description_
    """

    _check_ukrdc_extract(cohort)

    if assessment_type not in ["TPLTassess", "KRTassess"]:
        raise ValueError("assessment_type must be either 'TPLTassess' or 'KRTassess'")

    for column in ["pid", "sendingfacility", "fromtime"]:
        if column not in cohort.columns:
            raise MissingColumnError(f"cohort must contain '{column}' column")

    archive_sessionmaker = get_archive_sessionmaker(session)
    sending_facilities = cohort["sendingfacility"].unique().tolist()

    with archive_sessionmaker() as archive_session:
        careplanning_data = query_careplanning(archive_session, sending_facilities)

    # Map patient IDs using pid_ni_map
    pid_map = pid_ni_map(session, sending_facilities)
    careplanning_data = careplanning_data.merge(
        pid_map,
        on=["patientid", "organization", "sendingfacility"],
        how="left",
    )

    careplanning_data = careplanning_data[
        careplanning_data["assessmenttypecode"] == assessment_type
    ]
    careplanning_data = careplanning_data[
        [
            "pid",
            "assessmenttypecode",
            "assessmentstart",
            "assessmentend",
            "assessmentoutcomecode",
        ]
    ].copy()
    careplanning_data["assessmentoutcome"] = (
        careplanning_data["assessmentoutcomecode"]
        .map({"1": "Unsuitable", "2": "In-progress", "3": "Suitable"})
        .fillna("Other")
    )

    # merge_asof selects the latest assessment starting on or before fromtime
    careplanning_data = careplanning_data.dropna(subset=["pid", "assessmentstart"])
    cohort = pd.merge_asof(
        cohort.sort_values("fromtime"),
        careplanning_data.sort_values("assessmentstart"),
        left_on="fromtime",
        right_on="assessmentstart",
        by="pid",
        direction="backward",
    )
    cohort["assessmentoutcome"] = cohort["assessmentoutcome"].fillna("No assessment")

    return cohort


def link_first_seen(session: Session, cohort: pd.DataFrame) -> pd.DataFrame:
    """
    Labels a cohort with the date each patient was first seen by renal
    services and the centre that recorded it. This is recorded in the
    UKRDC as a treatment with modality code 101; the earliest fromtime is
    taken as the first_seen date.

    Args:
        session: UKRDC database session.
        cohort: DataFrame with a 'pid' column.

    Returns:
        The cohort with 'first_seen' and 'first_seen_centre' columns
        merged on 'pid'. Patients without a modality 101 record have
        first_seen = NaT and first_seen_centre = NaN.
    """

    if "pid" not in cohort.columns:
        raise MissingColumnError("cohort must contain 'pid' column")

    first_seen = query_first_seen(session, cohort["pid"].unique().tolist())

    return cohort.merge(first_seen, on="pid", how="left")


def eskd():
    pass


def vascular_access(
    cohort: pd.DataFrame,
    session,
    mode: str = "prevalent",
    prevalence_date: Optional[dt.date] = None,
) -> pd.DataFrame:
    """
    Labels a cohort with the vascular access (qhd20) recorded at the most
    recent dialysis session prior to a cutoff date. In prevalent mode the
    prevalence_date is used as the cutoff for every patient; in incident
    mode each patient's own KRT start date (fromtime) is used instead.
    """

    if "pid" not in cohort.columns:
        raise MissingColumnError("cohort must contain 'pid' column")

    if mode == "prevalent":
        if prevalence_date is None:
            raise ValueError("prevalence_date is required in prevalent mode")
        pids = cohort["pid"].unique().tolist()
        cutoff_dates = [prevalence_date] * len(pids)
    elif mode == "incident":
        # In this instance we get the vascular access used in the most recent
        # dialysis session 2 weeks on from the incidence date.
        if "fromtime" not in cohort.columns:
            raise MissingColumnError("cohort must contain 'fromtime' column")
        pairs = cohort[["pid", "fromtime"]].dropna().drop_duplicates(subset="pid")
        pids = pairs["pid"].tolist()
        cutoff_dates = [
            pair + dt.timedelta(days=14) for pair in pairs["fromtime"].tolist()
        ]
    else:
        raise ValueError("mode must be either 'prevalent' or 'incident'")

    access_data = query_vascular_access(session, pids, cutoff_dates)

    # join access data to cohort
    cohort = cohort.merge(access_data, on="pid", how="left")
    cohort.rename(
        columns={"procedure_time": "vascular_access_date", "qhd20": "access"},
        inplace=True,
    )
    cohort.loc[cohort["vascular_access_date"].isna(), "access"] = "Missing"

    return cohort


def hd_dialysis_frequency(session, patient_cohort, start, stop, mode="median"):
    # todo: check for other codes/code standards
    dialysis_snomed = ["302497006", "233581009", "233586004"]

    if "dialtplt" not in patient_cohort.columns:
        raise Exception("dialtplt column not found in patient_cohort")

    # retrieve dialysis sessions for hd patients
    hd_patients = (
        patient_cohort[patient_cohort["dialtplt"].isin(["HD", "HHD"])]["pid"]
        .unique()
        .tolist()
    )
    all_dialysis_data = []  # A list that will accept all the chunked dataframes
    for i in range(0, len(hd_patients), 100):
        chunk = hd_patients[i : i + 100]
        chunk_data = query_dialysis_sessions(session, chunk)
        # skip empty chunks so pd.concat infers dtypes from real data only
        if not chunk_data.empty:
            all_dialysis_data.append(chunk_data)

    if all_dialysis_data:
        dialysis_data = pd.concat(all_dialysis_data).drop_duplicates(
            subset=["pid", "procedure_time"]
        )
    else:
        dialysis_data = pd.DataFrame()

    if (
        not dialysis_data.empty
    ):  # need to test the combined dataframe not the list of dataframes
        adjusted_stop = start + pd.Timedelta(days=7 * ((stop - start).days // 7))
        dialysis_data = dialysis_data[
            (dialysis_data.procedure_time < adjusted_stop)
            & (dialysis_data.procedure_time >= start)
        ]
        dialysis_data["weekstart"] = (
            dialysis_data["procedure_time"].dt.to_period("W").dt.start_time
        )
        dialysis_data = dialysis_data[
            dialysis_data.procedure_type_code.isin(dialysis_snomed)
        ]

        # calculate weekly session count for each patient
        sessions_per_week = pd.DataFrame(
            {"weekstart": pd.date_range(start=start, end=adjusted_stop, freq="7D")}
        )
        session_counts = (
            dialysis_data.groupby(["pid", "weekstart"])["id"]
            .count()
            .reset_index(name="hdsessionno")
        )
        sessions_per_week = sessions_per_week.merge(
            session_counts, on="weekstart", how="left"
        )

        # require at least 3 weeks of data to be included
        week_counts = sessions_per_week.groupby("pid")["weekstart"].nunique()
        valid_pids = week_counts[week_counts >= 3].index
        sessions_per_week = sessions_per_week[
            sessions_per_week["pid"].isin(valid_pids)
        ].sort_values(["pid", "weekstart"])

        # Calculate median sessions per week per pid
        median_sessions = (
            sessions_per_week.groupby("pid")["hdsessionno"]
            .median()
            .astype(int)
            .reset_index()
        )
        median_sessions = median_sessions.rename(
            columns={"hdsessionno": "median_sessions_per_week"}
        )

        # print(median_sessions.head())
        median_sessions["sessions_binned"] = pd.cut(
            median_sessions["median_sessions_per_week"],
            bins=[0, 2, 3, 4, 1000],
            labels=["<2", "2", "3", ">3"],
            right=False,
        ).astype(str)

    else:
        median_sessions = pd.DataFrame(
            columns=["pid", "median_sessions_per_week", "sessions_binned"]
        )

    patient_cohort = patient_cohort.merge(
        median_sessions[["pid", "median_sessions_per_week", "sessions_binned"]],
        on="pid",
        how="left",
    )

    patient_cohort["sessions_binned"] = patient_cohort["sessions_binned"].fillna(
        "Unavailable"
    )

    return patient_cohort
