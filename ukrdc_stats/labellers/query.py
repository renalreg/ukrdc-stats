"""
################
LABELLER QUERIES
################

This module contains the raw queries which are used by the labellers to extract
ukrdc data. The scope of these functions should as far as possible be kept to
actually interacting with the database rather than processing and linking the
data.

"""

import zipfile
import shutil
from typing import List, Optional

import pandas as pd
import datetime as dt
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import Session

from ukrdc_sqla.ukrdc import (
    LabOrder,
    ResultItem, 
    Address,
    PatientRecord,
    Patient,
    DialysisSession,
    Facility
)
from ukrdc_sqla.utils.constants import FacilityType
from ukrdc_sqla.xmlarchive import Patient as XMLPatient, Assessment


from urllib.request import urlretrieve
from pathlib import Path

from ukrdc_stats.utils.cache import CONFIG

ONS_ADDRESS_DATA_URL = (
    "https://www.arcgis.com/sharing/rest/content/items/"
    "3635ca7f69df4733af27caf86473ffa1/data"
)

# ONS data lives alongside the query cache under the env-configured CACHE_DIR
ONS_CACHE_DIR = CONFIG["cache_dir"].parent / "ons_postcode_data"


def download_ons_address_data():
    """
    Download the ONS address data from the ArcGIS portal.
    """
    cache_dir = ONS_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    zip_path = cache_dir.parent / "ons_data.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    urlretrieve(ONS_ADDRESS_DATA_URL, zip_path)
    
    with open(zip_path, "rb") as f:
        header = f.read(4)
    if header[:2] != b"PK":
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
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

    return


def query_ons_postcode_data() -> pd.DataFrame:

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
    pids: List[str],
    test_codes: Optional[List[str]] = None,
    chunk_size: int = 100,
    from_time: Optional[dt.datetime] = None,
    to_time: Optional[dt.datetime] = None,
    mode: str = "raw",
) -> pd.DataFrame:
    """
    Extract test results for a cohort of patients using chunked queries
    to prevent database timeouts.

    Args:
        mode: "raw" returns all rows. "min" or "max" aggregates to one
              row per pid per serviceidcode using MIN/MAX(resultvalue),
              reducing data transfer for large result sets.
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
    session: Session, pids: List[str], chunk_size: int = 100
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
    df = pd.DataFrame(results)

    # Define preference order for addressuse

    if not results:
        return pd.DataFrame(columns=["pid", "postcode"])

    use_order = {"H": 1, "PST": 2, None: 3, "TMP": 4}
    df["use_priority"] = df["addressuse"].map(use_order).fillna(3)

    # Sort by pid and priority, then keep first occurrence per pid
    df = df.sort_values(["pid", "use_priority"]).groupby("pid", as_index=False).first()

    # Drop the priority column
    df = df.drop(columns=["use_priority", "addressuse"])

    return df

def query_careplanning(archieve_session: Session, facility_codes: List[str], prevalence_point: dt.datetime = None)->pd.DataFrame:
    """Extract care-planning assessments from the archive database. If a
    prevalence point is provided the most assessment prior to the prevelance 
    point is selected. 
    """
    
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
            Assessment.assessmentstart.desc()
        )


    prevalent_assessments = archieve_session.execute(assessments_query).all()
    if not prevalent_assessments:
        prevalent_assessments_df = pd.DataFrame(columns=[
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
            "assessmentoutcomecodedesc"
        ])
    
    else:
        prevalent_assessments_df = pd.DataFrame(prevalent_assessments)
    
    return prevalent_assessments_df

def query_vascular_access(
    session: Session,
    pids: List[str],
    cutoff_dates: List[dt.datetime],
    chunk_size: int = 100,
) -> pd.DataFrame:
    """Extract the most recent dialysis session prior to a patient specific date
    """

    if len(pids) != len(cutoff_dates):
        raise ValueError("pids and cutoff_dates must be the same length")

    pairs = list(zip(pids, cutoff_dates))
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

def query_paed_centres(session) -> list[str]:
    query = (
        select(Facility.facilitycode)
        .where(Facility.facilitytype == FacilityType.paediatric_renal_centre)
    )
    paed_centres = session.execute(query).all()

    return [row[0] for row in paed_centres]

def query_demog(session: Session, pids: List[str]) -> pd.DataFrame:
    """
    Extracts demographic data for a list of pids. Queries are chunked into
    groups of 100 to avoid connection timeouts caused by large IN clauses.
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


def query_dialysis_sessions(session: Session, patient_list: pd.Series):
    
    query = (
        select(
            DialysisSession.pid, 
            DialysisSession.id, 
            DialysisSession.procedure_type_code, 
            DialysisSession.procedure_time, 
            DialysisSession.qhd20
        ).where(DialysisSession.pid.in_(patient_list))
        .order_by(DialysisSession.pid, DialysisSession.procedure_time)
    )

    dialysis_sessions = pd.DataFrame(session.execute(query))
    if dialysis_sessions.empty:
        dialysis_sessions = pd.DataFrame(columns=["pid", "procedure_time", "qhd20"])

    return dialysis_sessions