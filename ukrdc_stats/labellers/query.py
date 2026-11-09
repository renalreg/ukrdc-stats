"""Labeller queries.

This module contains the raw queries which are used by the labellers to extract
ukrdc data. The scope of these functions should as far as possible be kept to
actually interacting with the database rather than processing and linking the
data.
"""

import datetime as dt
import shutil
import zipfile
from collections.abc import Sequence
from contextlib import suppress
from urllib.request import urlretrieve

import pandas as pd
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from ukrdc_sqla.ukrdc import (
    Address,
    DialysisSession,
    Facility,
    LabOrder,
    Patient,
    PatientRecord,
    ResultItem,
    Treatment,
)
from ukrdc_sqla.utils.constants import FacilityType
from ukrdc_sqla.xmlarchive import Assessment
from ukrdc_sqla.xmlarchive import Patient as XMLPatient

from ukrdc_stats.utils.cache import CONFIG

ONS_ADDRESS_DATA_URL = (
    "https://www.arcgis.com/sharing/rest/content/items/"
    "3635ca7f69df4733af27caf86473ffa1/data"
)

# ONS data lives alongside the query cache under the env-configured CACHE_DIR
ONS_CACHE_DIR = CONFIG["cache_dir"].parent / "ons_postcode_data"


def download_ons_address_data() -> None:
    """Download the ONS address data from the ArcGIS portal."""
    cache_dir = ONS_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    zip_path = cache_dir.parent / "ons_data.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    urlretrieve(ONS_ADDRESS_DATA_URL, zip_path)  # noqa: S310

    with zip_path.open("rb") as f:
        header = f.read(4)
    if header[:2] != b"PK":
        # don't let a cleanup failure mask the real error
        with suppress(OSError):
            zip_path.unlink(missing_ok=True)
        raise ValueError(
            "ONS download did not return a zip file. "
            "Check ONS_ADDRESS_DATA_URL and network access."
        )

    temp_extract = cache_dir.parent / "temp_extract"
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(temp_extract)

    source_file = temp_extract / "data" / "ONSPD_NOV_2025_UK.csv"
    if source_file.exists():
        shutil.copy2(source_file, cache_dir)

    shutil.rmtree(temp_extract)
    zip_path.unlink(missing_ok=True)


def query_ons_postcode_data() -> pd.DataFrame:
    """Load the ONS postcode IMD data, downloading it if not cached.

    Returns:
        pd.DataFrame: Postcodes with imd20ind and imddecile columns.
    """
    cache_dir = ONS_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    csv_path = cache_dir / "ONSPD_NOV_2025_UK.csv"
    if not csv_path.exists():
        download_ons_address_data()

    imd_data = pd.read_csv(
        csv_path,
        usecols=["pcd7", "imd20ind"],
        dtype={"pcd7": "string", "imd20ind": "int"},
        low_memory=False,
    ).drop_duplicates()

    imd_data["imddecile"] = pd.cut(
        imd_data["imd20ind"],
        bins=10,
        labels=[f"{i * 10}-{(i + 1) * 10}%" for i in range(10)],
        include_lowest=True,
    )

    return imd_data


def query_results(
    session: Session,
    pids: list[str],
    test_codes: list[str] | None = None,
    chunk_size: int = 100,
    from_time: dt.datetime | None = None,
    to_time: dt.datetime | None = None,
    mode: str = "raw",
) -> pd.DataFrame:
    """Extract test results for a cohort of patients using chunked queries.

    Chunking prevents database timeouts on large IN clauses.

    Args:
        session (Session): UKRDC database session.
        pids (list[str]): Patient ids to extract results for.
        test_codes (list[str] | None): serviceidcodes to filter on. None
            fetches everything.
        chunk_size (int): Patients per query chunk.
        from_time (dt.datetime | None): Earliest observationtime to include.
        to_time (dt.datetime | None): Latest observationtime to include.
        mode: "raw" returns all rows. "min" or "max" aggregates to one
              row per pid per serviceidcode using MIN/MAX(resultvalue),
              reducing data transfer for large result sets.

    Returns:
        pd.DataFrame: Result rows for the requested pids.
    """
    results = []

    for i in range(0, len(pids), chunk_size):
        chunk = pids[i : i + chunk_size]

        if mode in ("min", "max"):
            agg_func = func.min if mode == "min" else func.max
            query = (
                select(
                    LabOrder.pid,
                    ResultItem.serviceidcode,
                    agg_func(ResultItem.resultvalue).label("resultvalue"),
                    agg_func(ResultItem.resultvalueunits).label("resultvalueunits"),
                    agg_func(ResultItem.observationtime).label("observationtime"),
                )
                .join(ResultItem, ResultItem.orderid == LabOrder.id)
                .where(LabOrder.pid.in_(chunk))
                .group_by(LabOrder.pid, ResultItem.serviceidcode)
            )
        else:
            query = (
                select(
                    LabOrder.pid,
                    ResultItem.observationtime,
                    ResultItem.serviceidcode,
                    ResultItem.resultvalue,
                    ResultItem.resultvalueunits,
                )
                .join(ResultItem, ResultItem.orderid == LabOrder.id)
                .where(LabOrder.pid.in_(chunk))
            )

        if test_codes:
            query = query.where(ResultItem.serviceidcode.in_(test_codes))

        if from_time:
            query = query.where(ResultItem.observationtime >= from_time)

        if to_time:
            query = query.where(ResultItem.observationtime <= to_time)

        chunk_data = session.execute(query).all()

        if chunk_data:
            results.extend(chunk_data)

    columns = [
        "pid",
        "observationtime",
        "serviceidcode",
        "resultvalue",
        "resultvalueunits",
    ]

    if not results:
        return pd.DataFrame(columns=columns)

    return pd.DataFrame(results)


def query_postcodes(
    session: Session, pids: list[str], chunk_size: int = 100
) -> pd.DataFrame:
    """Extract postcodes for a cohort of patients.

    Returns a dataframe with one row per pid, selecting a single address by
    addressuse preference order: H, PST, NULL, TMP.
    """
    results = []

    for i in range(0, len(pids), chunk_size):
        chunk = pids[i : i + chunk_size]

        query = select(
            Address.pid,
            Address.postcode,
            Address.addressuse,
        ).where(Address.pid.in_(chunk))

        chunk_data = session.execute(query).all()
        if chunk_data:
            results.extend(chunk_data)
    postcodes_df = pd.DataFrame(results)

    # Define preference order for addressuse
    if not results:
        return pd.DataFrame(columns=["pid", "postcode"])
    use_order = {"H": 1, "PST": 2, None: 3, "TMP": 4}
    postcodes_df["use_priority"] = postcodes_df["addressuse"].map(use_order).fillna(3)

    # Sort by pid and priority, then keep first occurrence per pid
    postcodes_df = (
        postcodes_df.sort_values(["pid", "use_priority"])
        .groupby("pid", as_index=False)
        .first()
    )

    # Drop the priority column
    return postcodes_df.drop(columns=["use_priority", "addressuse"])


def query_careplanning(
    archieve_session: Session,
    facility_codes: list[str],
    assessment_type: Sequence[str] = ("TRTassess", "RRTassess", "PPDassess"),
    prevalence_point: dt.datetime | None = None,
) -> pd.DataFrame:
    """Extract care-planning assessments from the archive database.

    If a prevalence point is provided the most recent assessment prior to the
    prevalence point is selected.

    Args:
        archieve_session (Session): Session for the xml archive database.
        facility_codes (list[str]): Sending facilities to extract assessments
            for.
        assessment_type (Sequence[str], optional): Assessment type codes to
            include. Defaults to all three supported types.
        prevalence_point (dt.datetime, optional): Only assessments starting
            before this point are returned. Defaults to None.

    Returns:
        pd.DataFrame: DataFrame of assessments for the given facilities.
    """
    # retreive all the assessments for a given set of facilities and assessment_types
    assessments_query = (
        select(
            XMLPatient.nationalid.label("patientid"),
            XMLPatient.organization,
            XMLPatient.sendingfacility,
            XMLPatient.numbertype,
            XMLPatient.creation_date,
            Assessment.assessmentstart,
            Assessment.assessmentend,
            Assessment.assessmenttypecode,
            Assessment.assessmenttypecodestd,
            Assessment.assessmenttypecodedesc,
            func.trim(Assessment.assessmentoutcomecode).label("assessmentoutcomecode"),
            Assessment.assessmentoutcomecodestd,
            Assessment.assessmentoutcomecodedesc,
        )
        .join(
            Assessment,
            Assessment.patientid == XMLPatient.id,
        )
        .where(
            XMLPatient.sendingfacility.in_(facility_codes),
            Assessment.assessmenttypecode.in_(assessment_type),
        )
    )

    if prevalence_point:
        assessments_query = assessments_query.where(
            Assessment.assessmentstart < prevalence_point
        )
        assessments_query = assessments_query.distinct(
            XMLPatient.nationalid, XMLPatient.organization, XMLPatient.sendingfacility
        )
        assessments_query = assessments_query.order_by(
            XMLPatient.nationalid,
            XMLPatient.organization,
            XMLPatient.sendingfacility,
            Assessment.assessmentstart.desc(),
        )

    prevalent_assessments = archieve_session.execute(assessments_query).all()
    if not prevalent_assessments:
        prevalent_assessments_df = pd.DataFrame(
            columns=[
                "patientid",
                "organization",
                "sendingfacility",
                "numbertype",
                "creation_date",
                "assessmentstart",
                "assessmentend",
                "assessmenttypecode",
                "assessmenttypecodestd",
                "assessmenttypecodedesc",
                "assessmentoutcomecode",
                "assessmentoutcomecodestd",
                "assessmentoutcomecodedesc",
            ]
        )

    else:
        prevalent_assessments_df = pd.DataFrame(prevalent_assessments)

    return prevalent_assessments_df


def query_vascular_access(
    session: Session,
    pids: list[str],
    cutoff_dates: Sequence[dt.date],
    chunk_size: int = 100,
) -> pd.DataFrame:
    """Extract the most recent dialysis session prior to a patient-specific date.

    Args:
        session (Session): UKRDC database session.
        pids (list[str]): Patient ids to extract sessions for.
        cutoff_dates (Sequence[dt.date]): One cutoff per pid, zipped with
            pids. datetime and Timestamp are both accepted as they are
            date subtypes.
        chunk_size (int, optional): Patients per query chunk. Defaults to 100.

    Returns:
        pd.DataFrame: DataFrame with pid, procedure_time and qhd20 columns.

    Raises:
        ValueError: If pids and cutoff_dates are different lengths.
    """
    if len(pids) != len(cutoff_dates):
        raise ValueError("pids and cutoff_dates must be the same length")

    pairs = list(zip(pids, cutoff_dates, strict=False))
    results = []

    for i in range(0, len(pairs), chunk_size):
        chunk = pairs[i : i + chunk_size]
        query = (
            select(
                DialysisSession.pid,
                DialysisSession.procedure_time,
                DialysisSession.qhd20,
            )
            .where(
                or_(
                    *[
                        and_(
                            DialysisSession.pid == pid,
                            DialysisSession.procedure_time < cutoff,
                        )
                        for pid, cutoff in chunk
                    ]
                )
            )
            .distinct(DialysisSession.pid)
            .order_by(DialysisSession.pid, DialysisSession.procedure_time.desc())
        )

        chunk_data = session.execute(query).all()
        if chunk_data:
            results.extend(chunk_data)

    if not results:
        return pd.DataFrame(columns=["pid", "procedure_time", "qhd20"])

    return pd.DataFrame(results, columns=["pid", "procedure_time", "qhd20"])


def query_first_seen(
    session: Session, pids: list[str], chunk_size: int = 100
) -> pd.DataFrame:
    """Extract each patient's earliest modality-101 treatment and its centre.

    Modality code 101 records first contact with renal services. Queries
    are chunked to avoid connection timeouts caused by large IN clauses.
    """
    results = []
    for i in range(0, len(pids), chunk_size):
        chunk = pids[i : i + chunk_size]
        # DISTINCT ON takes the earliest 101 treatment per pid; id breaks
        # ties where fromtime is duplicated
        query = (
            select(
                Treatment.pid,
                Treatment.fromtime.label("first_seen"),
                Treatment.healthcarefacilitycode.label("first_seen_centre"),
            )
            .where(
                Treatment.pid.in_(chunk),
                Treatment.admitreasoncode == "101",
            )
            .distinct(Treatment.pid)
            .order_by(Treatment.pid, Treatment.fromtime, Treatment.id)
        )
        results.extend(session.execute(query).all())

    columns = ["pid", "first_seen", "first_seen_centre"]
    if not results:
        return pd.DataFrame(columns=columns)

    return pd.DataFrame(results, columns=columns)


def query_paed_centres(session: Session) -> list[str]:
    """Return the facility codes of all paediatric renal centres."""
    query = select(Facility.facilitycode).where(
        Facility.facilitytype == FacilityType.paediatric_renal_centre
    )
    paed_centres = session.execute(query).all()

    return [row[0] for row in paed_centres]


def query_demog(session: Session, pids: list[str]) -> pd.DataFrame:
    """Extract demographic data for a list of pids.

    Queries are chunked into groups of 100 to avoid connection timeouts
    caused by large IN clauses.
    """
    chunk_size = 100
    demog_data = []
    for i in range(0, len(pids), chunk_size):
        chunk = pids[i : i + chunk_size]
        query = (
            select(
                PatientRecord.pid,
                Patient.gender,
                Patient.birthtime,
                Patient.ethnicgroupcode,
            )
            .join(Patient, Patient.pid == PatientRecord.pid)
            .where(
                PatientRecord.pid.in_(chunk),
                PatientRecord.sendingextract == "UKRDC",
            )
        )
        demog_data.extend(session.execute(query).all())

    columns = ["pid", "gender", "birthtime", "ethnicgroupcode"]
    if not demog_data:
        return pd.DataFrame(columns=columns)

    return pd.DataFrame(demog_data, columns=columns)


def query_dialysis_sessions(
    session: Session, patient_list: Sequence[str]
) -> pd.DataFrame:
    """Extract dialysis sessions for a list of patients.

    Args:
        session (Session): UKRDC database session.
        patient_list (Sequence[str]): Patient ids to extract sessions for.

    Returns:
        pd.DataFrame: DataFrame with pid, procedure_type_code,
            procedure_time and qhd20 columns.
    """
    query = (
        select(
            DialysisSession.pid,
            DialysisSession.id,
            DialysisSession.procedure_type_code,
            DialysisSession.procedure_time,
            DialysisSession.qhd20,
        )
        .where(DialysisSession.pid.in_(patient_list))
        .order_by(DialysisSession.pid, DialysisSession.procedure_time)
    )

    dialysis_sessions = pd.DataFrame(session.execute(query))
    if dialysis_sessions.empty:
        dialysis_sessions = pd.DataFrame(columns=["pid", "procedure_time", "qhd20"])

    return dialysis_sessions
