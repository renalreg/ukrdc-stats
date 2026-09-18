"""
Module contains the queries to generate dataframes from raw ukrdc data with
minimal processing. Queries that can feasibly be empty should be generated
from the pandera schema. Queries which cannot feasibly be empty should return
an empty cohort error.

TODO:
- Expand validation via pandera schemas
"""

import datetime as dt
from ukrdc_stats.cohorts.schema import (
    krt_base_schema,
    krt_query_incident_schema,
    krt_query_prevalent_schema,
    demog_base_schema,
    ckd_ukrdc_base_schema,
    ckd_treatment_archive_base_schema,
)
from ukrdc_stats.utils.query import pid_ni_map
from ukrdc_sqla.ukrdc import (
    Treatment,
    Patient,
    PatientRecord,
    ModalityCodes,
    CodeMap,
    FacilityRelationship,
    LabOrder,
    ResultItem,
)
from ukrdc_sqla.utils.constants import RelationshipType
from ukrdc_sqla.xmlarchive import (
    Patient as XMLPatient,
    Treatment as XMLTreatment,
)

import pandera.pandas as pa
import pandas as pd
from ukrdc_stats.exceptions import EmptyCohortError
from sqlalchemy import select, exists, case, or_, and_, func
from sqlalchemy.orm import Session, aliased
from ukrdc_stats.utils.database import get_archive_sessionmaker


@pa.check_output(krt_query_prevalent_schema)
def query_krt_prevalent(
    session: Session,
    facility: str,
    prevalence_point: dt.datetime,
    recovery_window: dt.timedelta = dt.timedelta(days=90),
    sending_extract: str = "UKRDC",
) -> krt_query_prevalent_schema:
    """Core query containing all of the data from the ukrdc database
    necessary to generate a prevalent krt cohort.

    Returns:
        krt_query_prevalent_schema: Patient cohort dataframe
    """

    query = (
        select(
            PatientRecord.pid,
            PatientRecord.ukrdcid,
            PatientRecord.sendingfacility,
            PatientRecord.sendingextract,
            Treatment.healthcarefacilitycode,
            Treatment.admitreasoncode,
            Treatment.admitreasoncodestd,
            Treatment.admissionsourcecode,
            Treatment.admissionsourcecodestd,
            Treatment.qbl05,
            Treatment.hdp04,
            ModalityCodes.registry_code_type,
            Patient.birthtime,
            Patient.deathtime,
            Treatment.fromtime,
            Treatment.totime,
        )
        .select_from(PatientRecord)
        .join(Treatment, PatientRecord.pid == Treatment.pid)
        .join(Patient, PatientRecord.pid == Patient.id)
        .join(ModalityCodes, ModalityCodes.registry_code == Treatment.admitreasoncode)
        .outerjoin(
            FacilityRelationship,
            and_(
                Treatment.healthcarefacilitycode
                == FacilityRelationship.childfacilitycode,
                FacilityRelationship.relationshiptype
                == RelationshipType.main_satellite,
            ),
        )
        .where(
            or_(
                Treatment.healthcarefacilitycode == facility,
                FacilityRelationship.parentfacilitycode == facility,
            ),
            PatientRecord.sendingextract == sending_extract,
            (Patient.deathtime >= prevalence_point) | Patient.deathtime.is_(None),
            Treatment.fromtime <= prevalence_point + recovery_window,
            Treatment.totime.is_(None)
            | (Treatment.totime >= prevalence_point - recovery_window),
            ModalityCodes.registry_code_type.in_(["HD", "PD", "TX"]),
        )
    )

    data = session.execute(query).all()

    if not data:
        raise EmptyCohortError

    return pd.DataFrame(data)


@pa.check_output(krt_query_incident_schema)
def query_krt_incident(
    session: Session,
    centre_code: str,
    end: dt.datetime,
    start: dt.datetime,
    recovery_window: dt.timedelta = dt.timedelta(days=90),
    sending_extract: str = "UKRDC",
) -> krt_base_schema:
    """Core query containing all of the data from the ukrdc database
    necessary to generate an incident krt cohort.

    Returns:
        krt_base_schema: Patient cohort dataframe
    """

    minimum_transplant_length = 7

    ChronicTreatment = aliased(Treatment)
    ChronicModality = aliased(ModalityCodes)
    HistoricTransplantTreatment = aliased(Treatment)
    TransplantModality = aliased(ModalityCodes)
    SubPatientRecord = aliased(PatientRecord)

    # ukrdcids of patients treated at centre or its satellites
    ukrdc_sub = (
        select(PatientRecord.ukrdcid)
        .select_from(PatientRecord)
        .join(Treatment, PatientRecord.pid == Treatment.pid)
        .outerjoin(
            FacilityRelationship,
            and_(
                Treatment.healthcarefacilitycode
                == FacilityRelationship.childfacilitycode,
                FacilityRelationship.relationshiptype
                == RelationshipType.main_satellite,
            ),
        )
        .where(
            or_(
                Treatment.healthcarefacilitycode == centre_code,
                FacilityRelationship.parentfacilitycode == centre_code,
            ),
            PatientRecord.sendingextract == sending_extract,
        )
    )

    # ukrdcids with KRT treatment in the washout period — these are excluded
    washout_sub = (
        select(PatientRecord.ukrdcid)
        .select_from(PatientRecord)
        .join(Treatment, PatientRecord.pid == Treatment.pid)
        .join(ModalityCodes, ModalityCodes.registry_code == Treatment.admitreasoncode)
        .where(
            ModalityCodes.registry_code_type.in_(["HD", "PD", "TX"]),
            Treatment.fromtime >= (start - recovery_window),
            Treatment.fromtime < start,
        )
    )

    # ukrdcids with at least one KRT treatment in the incident window
    incident_sub = (
        select(PatientRecord.ukrdcid)
        .select_from(PatientRecord)
        .join(Treatment, PatientRecord.pid == Treatment.pid)
        .join(ModalityCodes, ModalityCodes.registry_code == Treatment.admitreasoncode)
        .where(
            ModalityCodes.registry_code_type.in_(["HD", "PD", "TX"]),
            Treatment.fromtime >= start,
            Treatment.fromtime <= end,
        )
    )

    # most recent CKD clinic record per patient (informational)
    ckd_ranked = (
        select(
            ChronicTreatment.pid.label("pid"),
            ChronicTreatment.healthcarefacilitycode.label("ckd_centre"),
            func.row_number()
            .over(
                partition_by=ChronicTreatment.pid,
                order_by=ChronicTreatment.fromtime.desc(),
            )
            .label("rn"),
        )
        .select_from(ChronicTreatment)
        .join(
            ChronicModality,
            ChronicModality.registry_code == ChronicTreatment.admitreasoncode,
        )
        .where(
            ChronicTreatment.fromtime < end,
            ChronicModality.registry_code_type == "CK",
        )
    ).subquery("ckd_ranked")

    ckd_latest = (
        select(ckd_ranked.c.pid, ckd_ranked.c.ckd_centre).where(ckd_ranked.c.rn == 1)
    ).subquery("ckd_latest")

    # check for historic transplant before the incident window
    # NOTE: this should probably include nhsbt feeds
    tx_check = exists().where(
        HistoricTransplantTreatment.pid == SubPatientRecord.pid,
        SubPatientRecord.ukrdcid == PatientRecord.ukrdcid,
        HistoricTransplantTreatment.fromtime < start,
        HistoricTransplantTreatment.totime - HistoricTransplantTreatment.fromtime
        > dt.timedelta(days=minimum_transplant_length),
        HistoricTransplantTreatment.admitreasoncode == TransplantModality.registry_code,
        TransplantModality.registry_code_type == "TX",
        SubPatientRecord.sendingextract == sending_extract,
    )

    # all KRT treatments for incident patients at this centre
    query = (
        select(
            PatientRecord.pid,
            PatientRecord.ukrdcid,
            PatientRecord.sendingfacility,
            PatientRecord.sendingextract,
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
            ModalityCodes.end_of_care,
            ModalityCodes.acute,
            ModalityCodes.transfer_in,
            Patient.deathtime,
            Patient.birthtime,
            Treatment.fromtime,
            Treatment.totime,
            ckd_latest.c.ckd_centre,
            case(
                (tx_check, True),
                else_=False,
            ).label("historic_tx"),
        )
        .select_from(PatientRecord)
        .join(Patient, Patient.pid == PatientRecord.pid)
        .join(Treatment, Treatment.pid == PatientRecord.pid)
        .join(ModalityCodes, ModalityCodes.registry_code == Treatment.admitreasoncode)
        .outerjoin(ckd_latest, ckd_latest.c.pid == PatientRecord.pid)
        .where(
            ModalityCodes.registry_code_type.in_(["HD", "PD", "TX"]),
            PatientRecord.ukrdcid.in_(ukrdc_sub),
            ~PatientRecord.ukrdcid.in_(washout_sub),
            PatientRecord.ukrdcid.in_(incident_sub),
            or_(Patient.deathtime > start, Patient.deathtime.is_(None)),
            PatientRecord.sendingextract == sending_extract,
        )
    )

    data = session.execute(query).all()

    if not data:
        raise EmptyCohortError

    return pd.DataFrame(data)


@pa.check_output(demog_base_schema)
def facility_demographics(session: Session, facility_code: str) -> krt_base_schema:
    """Core query to extract demographic information

    Args:
        session (Session): _description_
        facility_code (str): _description_

    Returns:
        krt_base_schema: _description_
    """
    query = (
        select(
            Patient.pid,
            Patient.birth_time,
            Patient.gender,
            Patient.ethnic_group_code,
            Patient.ethnic_group_code_std,
            Patient.ethnic_group_description,
        )
        .select_from(Patient)
        .join(PatientRecord, Patient.pid == PatientRecord.pid)
        .outerjoin(CodeMap, CodeMap.source_code == Patient.ethnic_group_code)
        .where(
            PatientRecord.facility == facility_code,
            CodeMap.source_coding_standard == "NHS_DATA_DICTIONARY",
            CodeMap.destination_coding_standard == "URTS_ETHNIC_GROUPING",
        )
    )
    data = session.execute(query).all()

    if data:
        demographics = pd.DataFrame(data)
    else:
        print(f"Warning no demographics extracted for facility {facility_code}")
        demographics = demog_base_schema.empty()

    return demographics


@pa.check_output(ckd_ukrdc_base_schema)
def query_ckd_ukrdc(
    session: Session,
    centre: str,
    prevalence_point: dt.datetime,
    extract_all: bool = False,
) -> ckd_ukrdc_base_schema:
    # Create an alias for Treatment to join on itself later
    Treatment2 = aliased(Treatment)

    # Correlated subqueries for eGFR data, pushed into SQL to avoid
    # fetching thousands of ResultItem rows into Python.
    lab_egfr_sub = (
        select(func.min(ResultItem.resultvalue))
        .join(LabOrder, ResultItem.orderid == LabOrder.id)
        .where(
            LabOrder.pid == PatientRecord.pid,
            ResultItem.serviceidcode.in_(["QBLAB", "QBLAL", "QBLAP"]),
            ResultItem.observationtime <= prevalence_point,
        )
        .correlate(PatientRecord)
        .scalar_subquery()
        .label("lab_egfr_min")
    )

    max_crea_val_sub = (
        select(ResultItem.resultvalue)
        .join(LabOrder, ResultItem.orderid == LabOrder.id)
        .where(
            LabOrder.pid == PatientRecord.pid,
            ResultItem.serviceidcode == "QBLA1",
            ResultItem.observationtime <= prevalence_point,
        )
        .order_by(ResultItem.resultvalue.desc())
        .limit(1)
        .correlate(PatientRecord)
        .scalar_subquery()
        .label("max_creatinine")
    )

    max_crea_units_sub = (
        select(ResultItem.resultvalueunits)
        .join(LabOrder, ResultItem.orderid == LabOrder.id)
        .where(
            LabOrder.pid == PatientRecord.pid,
            ResultItem.serviceidcode == "QBLA1",
            ResultItem.observationtime <= prevalence_point,
        )
        .order_by(ResultItem.resultvalue.desc())
        .limit(1)
        .correlate(PatientRecord)
        .scalar_subquery()
        .label("max_creatinine_units")
    )

    max_crea_date_sub = (
        select(ResultItem.observationtime)
        .join(LabOrder, ResultItem.orderid == LabOrder.id)
        .where(
            LabOrder.pid == PatientRecord.pid,
            ResultItem.serviceidcode == "QBLA1",
            ResultItem.observationtime <= prevalence_point,
        )
        .order_by(ResultItem.resultvalue.desc())
        .limit(1)
        .correlate(PatientRecord)
        .scalar_subquery()
        .label("max_creatinine_date")
    )

    query_ckd_patients = (
        select(
            PatientRecord.pid,
            PatientRecord.ukrdcid,
            PatientRecord.sendingfacility,
            PatientRecord.sendingextract,
            Patient.birthtime,
            Patient.deathtime,
            Treatment.healthcarefacilitycode,
            Treatment.healthcarefacilitydesc,
            Treatment.admitreasoncode,
            Treatment.admitreasoncodestd,
            Treatment.admitreasondesc,
            Treatment.fromtime,
            Treatment.totime,
            Patient.gender.label("sex"),
            Patient.ethnicgroupcode,
            Patient.ethnicgroupdesc,
            CodeMap.destination_code.label("ukkaethnicity"),
            ModalityCodes.registry_code_type,
            lab_egfr_sub,
            max_crea_val_sub,
            max_crea_units_sub,
            max_crea_date_sub,
        )
        .join(Treatment, Treatment.pid == PatientRecord.pid)
        .join(Patient, Patient.pid == PatientRecord.pid)
        .outerjoin(
            CodeMap,
            and_(
                CodeMap.source_code == Patient.ethnicgroupcode,
                CodeMap.source_coding_standard == Patient.ethnicgroupcodestd,
            ),
        )
        .outerjoin(
            FacilityRelationship,
            and_(
                Treatment.healthcarefacilitycode
                == FacilityRelationship.childfacilitycode,
                FacilityRelationship.relationshiptype
                == RelationshipType.main_satellite,
            ),
        )
        .where(
            ModalityCodes.registry_code_type.in_(["CK", "CN"]),
            or_(
                Treatment.healthcarefacilitycode == centre,
                FacilityRelationship.parentfacilitycode == centre,
            ),
            PatientRecord.sendingextract == "UKRDC",
            or_(
                Patient.deathtime > prevalence_point,
                Patient.deathtime.is_(None),
            ),
            or_(
                CodeMap.destination_coding_standard == "URTS_ETHNIC_GROUPING",
                CodeMap.destination_coding_standard.is_(None),
            ),
        )
    )

    if extract_all:
        # This option allows the extraction of all the CK treatments not just
        # those which overlap with the prevalence point.
        query_ckd_patients = (
            query_ckd_patients.join(Treatment2, Treatment2.pid == Treatment.pid)
            .join(
                ModalityCodes,
                ModalityCodes.registry_code == Treatment2.admitreasoncode,
            )
            .where(
                Treatment2.fromtime < prevalence_point,
                or_(
                    Treatment2.totime > prevalence_point,
                    Treatment2.totime.is_(None),
                ),
            )
        )
    else:
        query_ckd_patients = query_ckd_patients.join(
            ModalityCodes, ModalityCodes.registry_code == Treatment.admitreasoncode
        ).where(
            Treatment.fromtime < prevalence_point,
            or_(
                Treatment.totime > prevalence_point,
                Treatment.totime.is_(None),
            ),
        )

    query_ckd_patients = query_ckd_patients.order_by(PatientRecord.pid)
    data = session.execute(query_ckd_patients).all()

    if not data:
        raise EmptyCohortError

    base_cohort = pd.DataFrame(data)

    if not extract_all:
        base_cohort = base_cohort.sort_values(
            ["ukrdcid", "fromtime"], ascending=[True, False]
        ).drop_duplicates("ukrdcid", keep="first")

    return base_cohort


@pa.check_output(ckd_treatment_archive_base_schema)
def query_ckd_treatment_archive(
    archive_session: Session, facility: str, prevalence_point: dt.datetime
) -> ckd_treatment_archive_base_schema:
    """V5 corrections for ckd treatments in the archive.

    Args:
        facility (str): _description_
        prevalence_point (dt.datetime): _description_

    Returns:
        ckd_archive_base_schema: _description_
    """

    ckd_not_rrt_codes = ["901", "902", "903"]
    treatments_query = (
        select(
            XMLPatient.sendingfacility,
            XMLPatient.nationalid.label("patientid"),
            XMLPatient.organization,
            XMLPatient.numbertype,
            XMLTreatment.admitreasoncode,
            XMLTreatment.admitreasoncodestd,
            XMLTreatment.admitreasondesc,
            XMLTreatment.fromtime,
            XMLTreatment.totime,
        )
        .join(
            XMLTreatment,
            XMLTreatment.patientid == XMLPatient.id,
        )
        .where(
            XMLPatient.sendingfacility == facility,
            XMLTreatment.admitreasoncode.in_(ckd_not_rrt_codes),
            XMLTreatment.fromtime < prevalence_point,
            or_(
                XMLTreatment.totime > prevalence_point,
                XMLTreatment.totime.is_(None),
            ),
        )
    )
    data = archive_session.execute(treatments_query).all()

    if not data:
        output = ckd_treatment_archive_base_schema.empty()
    else:
        output = pd.DataFrame(data)

    return output


@pa.check_output(ckd_ukrdc_base_schema)
def query_ckd(session: Session, facility_code: str, prevalence_point: dt.datetime):
    """
    Function returns the base query which forms the basis of the ckd cohort.
    This includes data from the ukrdc with corrections from the removed xml_archive database.
    """

    # query the data in the ukrdc database
    ckd_ukrdc = query_ckd_ukrdc(session, facility_code, prevalence_point)

    # query the data in the archive database and map to ukrdc identifiers
    with get_archive_sessionmaker(session)() as archive_session:
        ckd_archive = query_ckd_treatment_archive(
            archive_session, facility_code, prevalence_point
        )

    if ckd_archive.empty:
        return ckd_ukrdc

    pid_ni_map_df = pid_ni_map(session, ckd_archive.sendingfacility.unique().tolist())
    ckd_archive_mapped = ckd_archive.merge(
        pid_ni_map_df,
        on=("patientid", "organization"),
        how="inner",
        suffixes=("", "_mapped"),
    )

    # carefully update ukrdc data with archive data
    ckd_ukrdc_corrected = ckd_ukrdc.merge(
        ckd_archive_mapped,
        on=("fromtime", "totime", "pid"),
        how="left",
        suffixes=("", "_archive"),
    )

    ckd_ukrdc_corrected.loc[
        ckd_ukrdc_corrected.admitreasoncode_archive.notnull(), "admitreasoncode"
    ] = ckd_ukrdc_corrected.loc[
        ckd_ukrdc_corrected.admitreasoncode_archive.notnull(), "admitreasoncode_archive"
    ]

    ckd_ukrdc_corrected.loc[
        ckd_ukrdc_corrected.admitreasoncode_archive.notnull(), "admitreasoncodestd"
    ] = ckd_ukrdc_corrected.loc[
        ckd_ukrdc_corrected.admitreasoncode_archive.notnull(),
        "admitreasoncodestd_archive",
    ]

    ckd_ukrdc_corrected.loc[
        ckd_ukrdc_corrected.admitreasoncode_archive.notnull(), "admitreasoncode"
    ] = ckd_ukrdc_corrected.loc[
        ckd_ukrdc_corrected.admitreasoncode_archive.notnull(), "admitreasoncode_archive"
    ]

    ckd_ukrdc_corrected.loc[
        ckd_ukrdc_corrected.admitreasoncode_archive.notnull(), "admitreasondesc"
    ] = ckd_ukrdc_corrected.loc[
        ckd_ukrdc_corrected.admitreasoncode_archive.notnull(), "admitreasondesc_archive"
    ]

    ckd_ukrdc_corrected.drop(
        columns=[
            "admitreasoncode_archive",
            "admitreasoncodestd_archive",
            "admitreasondesc_archive",
        ],
        inplace=True,
    )

    return ckd_ukrdc_corrected
