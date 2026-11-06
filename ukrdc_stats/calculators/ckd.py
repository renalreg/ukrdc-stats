"""Calculators associated with ckd. In particular the care planning."""

from operator import and_
import pandas as pd
import datetime as dt
from sqlalchemy import select, or_, create_engine, tuple_, case, func
from sqlalchemy.orm import Session, sessionmaker, aliased

from ukrdc_sqla.ukrdc import (
    Patient,
    PatientRecord,
    Treatment,
    Address,
    PatientNumber,
    ResultItem,
    LabOrder,
    CodeMap,
    ModalityCodes,
)
from ukrdc_sqla.xmlarchive import (
    Patient as XMLPatient,
    Assessment,
    Treatment as XMLTreatment,
)
from sqlalchemy.dialects.postgresql import distinct_on

from ukrdc_stats.calculators.abc import AbstractFacilityStatsCalculator
from ukrdc_stats.models.generic_2d import BaseTable
from ukrdc_stats.models.base import JSONModel
from ukrdc_stats.utils import egfr


def get_archive_session(session: Session) -> Session:
    # function to return a session of the xml v5 archive db
    # (xmlschemaconverter storage) not sure why this doesn't work
    db_url = session.bind.url

    password = db_url.password
    username = db_url.username
    host = db_url.host
    port = db_url.port
    drivername = db_url.drivername
    database = "removed_xml_archive"

    new_url = f"{drivername}://{username}:{password}@{host}:{port}/{database}"
    engine = create_engine(new_url)
    session = sessionmaker(bind=engine)

    with session() as archive_session:
        return archive_session


class CarePlanningReport(JSONModel):
    description: str = "prevalent ckd cohort..."
    cohort: str = "Prevalent CKD"
    table: BaseTable


class PrevalentCKDCalculator(AbstractFacilityStatsCalculator):
    def __init__(
        self,
        session: Session,
        facility: str,
        prevalence_point: dt.datetime = dt.datetime.now(),
        v5_archive_session: Session = None,
    ):
        super().__init__(session, facility)
        self._prevalence_point = prevalence_point

        if not v5_archive_session:
            self.v5_archive_session = get_archive_session(session)
        else:
            self.v5_archive_session = v5_archive_session

        self._ckd_not_rrt_codes = ["901", "902", "903"]

    def extract_stats(self):
        pass

    def _core_query(self, extract_all: bool = False):
        # get a single address per patient with a preference for home address
        address_ranked = (
            select(
                Address.pid,
                Address.postcode,
                Address.addressuse,
                func.row_number()
                .over(
                    partition_by=Address.pid,
                    order_by=case((Address.addressuse == "H", 0), else_=1),
                )
                .label("rn"),
            ).where(Address.postcode.is_not(None), func.trim(Address.postcode) != "")
        ).subquery()

        # Create an alias for Treatment to join on itslef later
        Treatment2 = aliased(Treatment)

        query_ckd_patients = (
            select(
                PatientRecord.pid,
                PatientRecord.ukrdcid,
                PatientRecord.sendingfacility,
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
                address_ranked.c.postcode,
                address_ranked.c.addressuse,
                Patient.ethnicgroupcode,
                Patient.ethnicgroupdesc,
                CodeMap.destination_code.label("ukkaethnicity"),
                ModalityCodes.registry_code_type,
            )
            .join(Treatment, Treatment.pid == PatientRecord.pid)
            .join(Patient, Patient.pid == PatientRecord.pid)
            .outerjoin(address_ranked, address_ranked.c.pid == PatientRecord.pid)
            .outerjoin(
                CodeMap,
                and_(
                    CodeMap.source_code == Patient.ethnicgroupcode,
                    CodeMap.source_coding_standard == Patient.ethnicgroupcodestd,
                ),
            )
            .join(PatientNumber, PatientNumber.pid == PatientRecord.pid, isouter=True)
            .where(
                ModalityCodes.registry_code_type.in_(["CK", "CN"]),
                PatientRecord.sendingfacility == self.facility,
                PatientRecord.sendingextract == "UKRDC",
                or_(
                    Patient.deathtime > self._prevalence_point,
                    Patient.deathtime.is_(None),
                ),
                or_(
                    CodeMap.destination_coding_standard == "URTS_ETHNIC_GROUPING",
                    CodeMap.destination_coding_standard.is_(None),
                ),
                address_ranked.c.rn == 1,
            )
        )

        if extract_all:
            query_ckd_patients = (
                query_ckd_patients.join(Treatment2, Treatment2.pid == Treatment.pid)
                .join(
                    ModalityCodes,
                    ModalityCodes.registry_code == Treatment2.admitreasoncode,
                )
                .where(
                    Treatment2.fromtime < self._prevalence_point,
                    or_(
                        Treatment2.totime > self._prevalence_point,
                        Treatment2.totime.is_(None),
                    ),
                )
            )
        else:
            query_ckd_patients = query_ckd_patients.join(
                ModalityCodes, ModalityCodes.registry_code == Treatment.admitreasoncode
            ).where(
                Treatment.fromtime < self._prevalence_point,
                or_(
                    Treatment.totime > self._prevalence_point,
                    Treatment.totime.is_(None),
                ),
            )

        query_ckd_patients = query_ckd_patients.order_by(PatientRecord.pid)

        base_cohort = (
            pd.DataFrame(self.session.execute(query_ckd_patients))
            .drop_duplicates()
            .reset_index(drop=True)
        )

        if not extract_all:
            base_cohort = base_cohort.sort_values(
                ["pid", "fromtime"], ascending=[True, False]
            ).drop_duplicates("pid", keep="first")

        return base_cohort

    def _get_patient_numbers(self, pids: list[str]) -> pd.DataFrame:
        CHUNK_SIZE = 100
        all_patient_numbers = []

        # Process PIDs in specified chunks
        for i in range(0, len(pids), CHUNK_SIZE):
            chunk_pids = pids[i : i + CHUNK_SIZE]

            query = select(
                PatientNumber.pid,
                PatientNumber.patientid,
                PatientNumber.organization,
                PatientNumber.numbertype,
            ).where(
                PatientNumber.pid.in_(chunk_pids),
            )

            chunk_results = pd.DataFrame(self.session.execute(query)).drop_duplicates()
            if not chunk_results.empty:
                all_patient_numbers.append(chunk_results)

        # Combine all chunks
        if all_patient_numbers:
            patients_numbers = pd.concat(all_patient_numbers, ignore_index=True)
        else:
            patients_numbers = pd.DataFrame(
                columns=["pid", "patientid", "organization", "numbertype"]
            )

        return patients_numbers.reset_index(drop=True).astype(str)

    def _get_archive_data(
        self, patient_numbers: pd.DataFrame, extract_all: bool = False
    ):
        # Break up large queries into chunks to avoid PostgreSQL stack overflow
        BATCH_SIZE = 100
        all_assessments = []
        all_treatments = []

        # Process patient numbers in batches
        for i in range(0, len(patient_numbers), BATCH_SIZE):
            batch = patient_numbers.iloc[i : i + BATCH_SIZE]

            # Assessments query for this batch
            assessments_query = (
                select(
                    XMLPatient.nationalid.label("patientid"),
                    XMLPatient.organization,
                    XMLPatient.numbertype,
                    XMLPatient.creation_date,
                    Assessment.assessmentstart,
                    Assessment.assessmentend,
                    Assessment.assessmenttypecode,
                    Assessment.assessmenttypecodestd,
                    Assessment.assessmenttypecodedesc,
                    Assessment.assessmentoutcomecode,
                    Assessment.assessmentoutcomecodestd,
                    Assessment.assessmentoutcomecodedesc,
                )
                .join(
                    Assessment,
                    Assessment.patientid == XMLPatient.id,
                )
                .where(
                    Assessment.assessmentstart < self._prevalence_point,
                    tuple_(
                        XMLPatient.nationalid,
                        XMLPatient.organization,
                        XMLPatient.numbertype,
                    ).in_(
                        list(
                            zip(
                                batch["patientid"],
                                batch["organization"],
                                batch["numbertype"],
                            )
                        )
                    ),
                )
            )

            # Treatments query for this batch
            treatments_query = (
                select(
                    XMLPatient.nationalid.label("patientid"),
                    XMLPatient.organization,
                    XMLPatient.numbertype,
                    XMLPatient.creation_date,
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
                    XMLTreatment.admitreasoncode.in_(self._ckd_not_rrt_codes),
                    tuple_(
                        XMLPatient.nationalid,
                        XMLPatient.organization,
                        XMLPatient.numbertype,
                    ).in_(
                        list(
                            zip(
                                batch["patientid"],
                                batch["organization"],
                                batch["numbertype"],
                            )
                        )
                    ),
                )
            )

            if not extract_all:
                treatments_query = treatments_query.where(
                    XMLTreatment.fromtime < self._prevalence_point,
                    or_(
                        XMLTreatment.totime > self._prevalence_point,
                        XMLTreatment.totime.is_(None),
                    ),
                )

            # Execute queries and collect results
            batch_assessments = pd.DataFrame(
                self.v5_archive_session.execute(assessments_query)
            )
            if not batch_assessments.empty:
                all_assessments.append(batch_assessments)

            batch_treatments = pd.DataFrame(
                self.v5_archive_session.execute(treatments_query)
            )
            if not batch_treatments.empty:
                all_treatments.append(batch_treatments)

        # Combine results from all batches
        if all_assessments:
            assessments = pd.concat(all_assessments).reset_index(drop=True)
        else:
            assessments = pd.DataFrame(
                columns=[
                    "patientid",
                    "organization",
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

        if all_treatments:
            treatments = pd.concat(all_treatments).reset_index(drop=True)
        else:
            treatments = pd.DataFrame(
                columns=[
                    "patientid",
                    "organization",
                    "numbertype",
                    "creation_date",
                    "admitreasoncode",
                    "admitreasoncodestd",
                    "admitreasondesc",
                    "fromtime",
                    "totime",
                ]
            )

        # drop ids and deduplicate (incase same patient has been written multiple times)
        assessments = pd.merge(
            assessments,
            patient_numbers,
            on=["patientid", "organization", "numbertype"],
            how="inner",
        )
        treatments = pd.merge(
            treatments,
            patient_numbers,
            on=["patientid", "organization", "numbertype"],
            how="inner",
        )

        assessments = assessments.drop(
            columns=["patientid", "organization", "numbertype"]
        ).drop_duplicates()
        treatments = treatments.drop(
            columns=["patientid", "organization", "numbertype"]
        ).drop_duplicates()

        return treatments, assessments

    def _get_test_results(self, patient_ids):
        """gets the most recent creatinine and lab egfr
        "QBLA1", "QBLAB", "QBLAL"
        Args:
            patient_ids (_type_):
        """

        query = (
            select(
                LabOrder.pid,
                ResultItem.serviceidcode,
                ResultItem.resultvalue,
                ResultItem.resultvalueunits,
                ResultItem.observationtime,
            )
            .ext(distinct_on(LabOrder.pid, ResultItem.serviceidcode))
            .join(LabOrder, LabOrder.id == ResultItem.order_id)
            .where(
                ResultItem.serviceidcode.in_(["QBLA1", "QBLAB", "QBLAL", "QBLAP"]),
                LabOrder.pid.in_(patient_ids),
                ResultItem.observation_time < self._prevalence_point,
            )
            .order_by(
                LabOrder.pid,
                ResultItem.serviceidcode,
                ResultItem.observation_time.desc(),
            )
        )

        results = pd.DataFrame(self.session.execute(query).all())
        if results.empty:
            columns = [
                "pid",
                "serviceidcode",
                "resultvalue",
                "resultvalueunits",
                "observationtime",
            ]
            results = pd.DataFrame(columns=columns)

        # separate and clean
        egfr_results = results[
            results["serviceidcode"].isin(["QBLAB", "QBLAP", "QBLAL"])
        ].copy()
        egfr_results["resultvalue"] = (
            egfr_results["resultvalue"].str.replace("<", "").str.replace(">", "")
        )
        egfr_results["resultvalue"] = pd.to_numeric(
            egfr_results["resultvalue"], errors="coerce"
        )

        # Drop rows where eGFR conversion to numeric failed then deduplicate
        egfr_results = egfr_results.dropna(subset=["resultvalue"])

        egfr_results = egfr_results.sort_values("observationtime").drop_duplicates(
            subset=["pid"], keep="last"
        )

        # Get creatinine results
        creatinine_results = results[
            results["serviceidcode"] == "QBLA1"
        ]  # .drop(columns=['serviceidcode'])
        creatinine_results["resultvalue"] = pd.to_numeric(
            creatinine_results["resultvalue"], errors="coerce"
        )

        # Merge creatinine and eGFR results
        merged_results = pd.merge(
            creatinine_results,
            egfr_results,
            on="pid",
            how="outer",
            suffixes=("_creat", "_labegfr"),
        )

        return merged_results

    def _extract_base_patient_cohort(self, extract_all: bool = False):
        # Get main cohort from ukrdc
        cohort = self._core_query(extract_all)

        if cohort.empty:
            return

        # Get all know patient identifiers for matching
        patient_numbers = self._get_patient_numbers(cohort["pid"].tolist())

        # Send patient numbers to the archive to extract data from there
        treatments, assessments = self._get_archive_data(patient_numbers, extract_all)

        # correct the treatments using the archive data
        # we assume treatments without corresponding ukrdc record are invalid
        # this could/should be restricted to codes that can map to eachother
        # e.g. 902 -> 900 where like '9%' or something
        cohort = pd.merge(
            cohort,
            treatments,
            on=["pid", "fromtime", "totime"],
            how="left",
            suffixes=("_ukrdc", ""),
        )

        # add in treatments not in the archive and drop ukrdc values
        ukrdc_only = cohort["admitreasoncode"].isnull()
        cohort.loc[
            ukrdc_only, ["admitreasoncode", "admitreasoncodestd", "admitreasondesc"]
        ] = cohort.loc[
            ukrdc_only,
            [
                "admitreasoncode_ukrdc",
                "admitreasoncodestd_ukrdc",
                "admitreasondesc_ukrdc",
            ],
        ].values
        cohort = cohort.drop(
            columns=[
                "admitreasoncode_ukrdc",
                "admitreasoncodestd_ukrdc",
                "admitreasondesc_ukrdc",
            ]
        )

        # join assessments
        cohort = pd.merge(
            cohort,
            assessments,
            on=["pid"],
            how="left",
            suffixes=("_treatment", "_assessment"),
        )

        test_results = self._get_test_results(cohort["pid"].tolist())

        # get test results and cakculate egfr
        cohort = pd.merge(
            cohort,
            test_results,
            on=["pid"],
            how="left",
        )

        cohort["calculated_egfr"] = cohort.apply(
            lambda row: egfr(
                row["resultvalue_creat"],
                row["resultvalueunits_creat"],
                row["observationtime_creat"],
                row["birthtime"],
                row["sex"],
                row["ukkaethnicity"],
            ),
            axis=1,
        )

        # Get universal patient ids (NHS number ideally)
        patient_ids_sorted = (
            patient_numbers.drop(columns=["numbertype"])
            .sort_values(
                by=["organization"],
                key=lambda x: x.map({"NHS": 0, "CHI": 1, "HSC": 2, "LOCALHOSP": 3}),
            )
            .drop_duplicates(subset=["pid"], keep="first")
        )

        # Merge patient them back into the cohort
        cohort = pd.merge(
            cohort,
            patient_ids_sorted,
            on=["pid"],
            how="left",
        )

        cohort.rename(columns={"patientid": "externalid"}, inplace=True)

        return cohort

    def _extract_patient_cohort(self, extract_all: bool = True):
        self._patient_cohort = self._extract_base_patient_cohort(extract_all)
