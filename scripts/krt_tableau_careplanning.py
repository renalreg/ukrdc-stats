"""
Example of how to extend the dashboard stats functionality using pandas to
produce an extract which is compatible with the tableau visualisations on the
UKKidney website here:
https://www.ukkidney.org/audit-research/data-portals

Hosted here:
https://public.tableau.com/app/profile/ukkidney/viz/KRTlandingpage/Landingpage

This functionality is still very much in development so it should be treated
with care yada yada.
"""
#   James M 08-Oct-25   I have altered line 150 to only exclude assessments which happened after KRT start, but leave in the blanks


import os
import argparse
import datetime as dt
import pandas as pd
from pathlib import Path

from rr_connection_manager import PostgresConnection
from ukrdc_stats.calculators.krt import KRTStatsCalculator
from ukrdc_stats.exceptions import NoCohortError
from ukrdc_stats.calculators.demographics import DemographicStatsCalculator, GENDER_GROUP_MAP
from ukrdc_stats.calculators.ckd import get_archive_session
from ukrdc_stats.utils import map_codes, check_headcounts
from sqlalchemy import select
from ukrdc_sqla.xmlarchive import Assessment, Patient
from ukrdc_sqla.ukrdc import PatientNumber, PatientRecord
from sqlalchemy.dialects.postgresql import distinct_on


# Get arguments from command line
parser = argparse.ArgumentParser(description='Extract CKD demographics data')
parser.add_argument('--year-start', type=int, default=2024, help='Starting year for extraction (default: 2024)')
parser.add_argument('--quarter-start', type=int, default=3, help='Starting quarter (1-4) for extraction (default: 3)')
parser.add_argument('--no-of-quarters', type=int, default=4, help='Number of quarters to extract (default: 4)')
parser.add_argument('--output-dir', type=str, default='.do_not_commit', help='Output directory for CSV file (default: .do_not_commit)')
args = parser.parse_args()



# Configuration
YEAR_START = args.year_start
QUARTER_START = args.quarter_start
NO_OF_QUARTERS = args.no_of_quarters
OUTPUT_DIR = Path(args.output_dir)

OUTPUT_FILE = "krt_care_planning"
SERVER =  "ukrdc_live"
OUTPUT_FILE = f"{OUTPUT_FILE}_{SERVER}_{YEAR_START}.csv"
os.makedirs(OUTPUT_DIR, exist_ok=True)

FACILITIES = [
    "RAJ",
    "RAQ01",
    "RCSLB",
    "RH8",
    "RHW01",
#    "RJZ", Kings
    "RK7CC",
    "RL403", 
    "RNJ00",
]

def get_facility_assessments(ukrdc_session, facility):
    """Get all assessments for a facility and link to the ukrdc patient records

    Args:
        archive_session (_type_): _description_
        facility (_type_): _description_

    Returns:
        _type_: _description_
    """
    archive_session = get_archive_session(ukrdc_session)

    # get assessments for facility
    query = select(
        Patient.nationalid,
        Patient.organization,
        Assessment.assessmentstart,
        Assessment.assessmentend,
        Assessment.assessmentoutcomecode
    ).join(
        Patient, Assessment.patientid == Patient.id
    ).where(
        Patient.sendingfacility == facility, 
        Assessment.assessmentoutcomecode.in_(["1", "2", "3"]),
        Assessment.assessmenttypecode == "TPLTassess"
    )
    assessments = pd.DataFrame(archive_session.execute(query))

    # get pids to link to the ukrdc patients 
    query = select(
        PatientNumber.pid,
        PatientNumber.patientid,
        PatientNumber.organization
    ).ext(
        distinct_on(
            PatientNumber.pid,
            PatientNumber.patientid,
            PatientNumber.organization
        )
    ).join(
        PatientRecord, PatientNumber.pid == PatientRecord.pid
    ).where(
        PatientRecord.sendingfacility == facility
    )
    pids = pd.DataFrame(ukrdc_session.execute(query))
    pids.rename(columns={"patientid":"nationalid"}, inplace=True)

    if pids.empty or assessments.empty:
        return pd.DataFrame(columns=["pid","nationalid", "organization", "assessmentstart", "assessmentend", "assessmentoutcomecode"])

    # link assessments to pids
    assessments = pd.merge(assessments, pids, on=["nationalid", "organization"], how="left")
    assessments.drop(columns=["organization", "nationalid"], inplace=True)

    assessments["assessmentoutcomecode"] = assessments["assessmentoutcomecode"].map({"1":"Unsuitable", "2":"In-progress", "3":"Suitable"})

    return assessments.drop_duplicates()

def krt_care_planning_cohort(assessments, ukrdc_session, facility, year, quarter):
    """Function to define the krt/careplanning cohort
    """
    quarter_start = dt.datetime(year, quarter*3-2, 1)
    if quarter < 4:
        quarter_end = dt.datetime(year, quarter*3 +1, 1) - dt.timedelta(days=1)
    else:
        quarter_end = dt.datetime(year, 12, 31) 
    
    
    krt_calculator = KRTStatsCalculator(
        session=ukrdc_session, 
        facility=facility, 
        from_time=quarter_start, 
        to_time=quarter_end
    )

    # create reports on incident and prevalent patients
    incident_krt_report = (
        krt_calculator.generate_cohort_report(
            cohort="incident"
        ).table.to_pandas()
    )
     

    # Mash them together and do some remapping 
    incident_krt_report.rename(
        columns = {
            "registry_code_type":"dialtplt",
            "sendingfacility":"centre_code",
            "healthcarefacilitycode":"satellite_code"
        },
        inplace=True
    )
    incident_krt_report["dialtplt"] = incident_krt_report["dialtplt"].map({"PD":"PD","TX":"Transplant","HD":"HD"})


    # merge and drop rows where there is an outcomecode but the assessment start > treatment start
    incident_krt_report = pd.merge(incident_krt_report, assessments, on="pid", how="left")
    incident_krt_report = incident_krt_report[
        (incident_krt_report.assessmentstart < incident_krt_report.fromtime)
        | (incident_krt_report.assessmentstart.isna())
    ]

    incident_krt_report = incident_krt_report.sort_values(by=['pid', 'assessmentstart'], ascending=[True, False])
    incident_krt_report = incident_krt_report.drop_duplicates(subset=['pid'], keep='first')

    incident_krt_report.drop(columns = ["pid", "assessmentstart","assessmentend","admitreasoncode","admitreasoncodestd"], inplace=True)
    incident_krt_report.fillna("No assessment", inplace=True)
    
    return incident_krt_report

def apply_demographic_aggregation(cohort,ukrdc_session,facility,date):
    """
    Function to aggregate demographics data in a tableau digestible way
    """

    # initialise demographics and create report
    demographics_calculator = DemographicStatsCalculator(
        session=ukrdc_session,
        facility=facility,
        date = date
    )

    try:
        _, demographics_report = demographics_calculator.produce_report(
            output_columns=["gender", "age", "ethnic_group_code"]
        )
    except NoCohortError:
        return pd.DataFrame(columns=["satellite_code", "variable", "dialtplt", "assessmentoutcomecode", "value"])
    
    demographics_report = demographics_report.to_pandas()
    demographics_report = demographics_report[demographics_report["age"].astype(int) > 18]

    # Modality total headcount 
    total = pd.merge(cohort, demographics_report[["ukrdcid"]], on="ukrdcid")
    total["variable"] = total["dialtplt"]
    total.drop_duplicates(inplace=True)
    total = total.groupby(["satellite_code", "variable", "dialtplt", "assessmentoutcomecode"]).size().reset_index(name="value")

    # Aggregate gender
    gender = pd.merge(cohort, demographics_report[["ukrdcid","gender"]], on="ukrdcid")
    gender["variable"] = gender["gender"].map(GENDER_GROUP_MAP)
    gender.drop(columns = ["gender"], inplace=True)
    gender.drop_duplicates(inplace=True)
    gender = gender.groupby(["satellite_code", "variable", "dialtplt", "assessmentoutcomecode"]).size().reset_index(name="value")

    # Aggregate age 
    age = pd.merge(cohort, demographics_report[["ukrdcid","age"]], on="ukrdcid")
    age["value"] = age["age"].astype(int)
    bins = [18, 35, 55, 75, 150]
    labels = ["18-34", "35-54", "55-74", ">=75"]
    age["variable"] = pd.cut(age["value"], bins=bins, labels=labels, right=False)
    age.drop_duplicates(inplace=True)
    age = age.groupby(["satellite_code", "variable", "dialtplt", "assessmentoutcomecode"]).size().reset_index(name="value")

    # Aggregate ethnicity
    ethnic_group_map = map_codes("NHS_DATA_DICTIONARY", "URTS_ETHNIC_GROUPING", ukrdc_session)
    ethnicity = pd.merge(cohort, demographics_report[["ukrdcid","ethnic_group_code"]], on="ukrdcid")
    ethnicity["variable"] = ethnicity["ethnic_group_code"].map(ethnic_group_map)
    ethnicity.drop(columns = ["ethnic_group_code"], inplace=True )
    ethnicity.drop_duplicates(inplace=True)
    ethnicity = ethnicity.groupby(["satellite_code", "variable", "dialtplt", "assessmentoutcomecode"]).size().reset_index(name="value")

    # Combine dataframes together
    gender["variable2"] = "Sex"
    ethnicity["variable2"] = "Ethnicity"
    age["variable2"] = "Age"
    total["variable2"] = "KRT Type"

    return pd.concat([gender, ethnicity, age, total])


# Connect to database
conn = PostgresConnection(app=SERVER, tunnel=True, via_app=True)
sessionmaker = conn.session_maker()

single_quarter_cohorts = []


with sessionmaker() as session:    
    # Get some mapping to relation sending facilities to regions
    #region_map = map_codes("RR1", "URTS_region", session)
    #facility_names = lookup_codes("RR1+", "description", session)
    for facility in FACILITIES:
        assessments = get_facility_assessments(session, facility)
        for q_offset in range(QUARTER_START - 1, QUARTER_START + NO_OF_QUARTERS - 1):
            current_quarter = (q_offset % 4) + 1
            current_year = YEAR_START + q_offset // 4
            granular_cohort =  krt_care_planning_cohort(assessments, session, facility, current_year, current_quarter)
            # Set demographics snapshot to quarter end, matching cohort timing
            if current_quarter < 4:
                quarter_end = dt.datetime(current_year, current_quarter*3 + 1, 1) - dt.timedelta(days=1)
            else:
                quarter_end = dt.datetime(current_year, 12, 31)

            
            cohort = apply_demographic_aggregation(granular_cohort, session, facility, quarter_end)
            

            cohort["year"] = current_year
            cohort["quarter"] = current_quarter
            cohort["country"] = "England"
            
            cohort["centre_code"] = facility
            cohort.rename(columns={"assessmentoutcomecode":"assessmentoutcome"}, inplace=True)
            single_quarter_cohorts.append(cohort)
            try:
                if not cohort.empty:
                    check_headcounts(cohort[["satellite_code", "centre_code","variable", "assessmentoutcome", "quarter", "variable2", "value"]].drop_duplicates())
            except Warning as e:
                print(e)

output_order = [
    "variable",
    "dialtplt",
    "country",
    "variable2",
    "centre_code",
    "satellite_code",
    "year",
    "quarter",
    "assessmentoutcome",
    "value",
]

pd.concat(single_quarter_cohorts).to_csv(
    os.path.join(OUTPUT_DIR, OUTPUT_FILE), 
    index=False
)