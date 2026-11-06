"""
Module to contain the long descriptions for the pydantic output
"""

from textwrap import dedent


dialysis_general = """
- Patients are queried on the basis of their treatment records overlapping the time window. 
- 
"""

dialysis_descriptions = {
    "ALL_PATIENTS_KRT_COHORT": dedent(
        """
        # All Patients Undergoing Kidney Replacement Therapy

        ## Overview
        This pie chart illustrates the proportion of patients who received kidney replacement therapy within the time period. The chart is broken down by the type of treatment, including HD In-center, HD Home, HD Unknown/Incomplete, PD, and Tx. Optionally the chart can be filtered by satellite unit. 

        ## Treatment Definitions
        - HD: Haemodialysis patients (with a modality defined as HD by the UKRDC). This includes patients registered for haemodialysis, haemofiltration, haemodiafiltration, or ultrafiltration. 
        - PD: Peritoneal dialysis (with a modality defined as PD by the UKRDC).This includes patients registered for CAPD or APD treatments.
        - TX: Transplant patients (with a modality defined as TX), including both living and cadaver donors.
        - In-centre: HD patients with qbl05 field of the Treatment table as HOSP or SATL.   
        - Home: HD patients with qbl05 field of the Treatment table as HOME. 
        - Unknown/Incomplete: HD patients with incomplete qbl05 field or anything other than HOME, HOSP, or SATL

        ## Methodology 
        - Any patients with a time of death before the beginning of the time window were excluded from the cohort, as were any patients whose treatments started before and ended after it.
        - Patient's therapy types was selected using the admission reason and the unit, and were further split into home and in-center therapy groups (with all patients on PD included in the home therapies group).
        - The numbers were calculated from aggregating patients within the five groups: HD Home, HD In-centre, HD Unknown/Incomplete, PD and TX.
        - No deduplication is applied to the treatment records so patients with multiple treatments will be double counted
                
        ## UKRDC Entities Used
        The chart was produced by joining the following UKRDC entities according to their foreign key relationships:
        - [PatientRecord](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450149/PatientRecord): ukrdcid, sendingextract
        - [Patient](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450145/Patient): deathtime
        - [Treatment](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450155/Treatment+Encounter): qbl05, hdp04, fromtime, totime, dischargereasoncode, healthcarefacilitycode
        - [ModalityCodes](https://renalregistry.atlassian.net/l/cp/Ac1YeFfH): registry_code_type
        """
    ),
    "INCIDENT_KRT_COHORT": dedent(
        """
        # Incident Kidney Replacement Patients

        ## Definition
        A patient starting kidney replacement therapy (KRT) - defined as haemodialysis, peritoneal dialysis, 
        or kidney transplant - for the first time during the selected time period.

        ## Inclusion Criteria
        1. First treatment starts within the selected dates. This is defined as the first treatment preceded by a gap of greater than 90 days without KRT treatment.
        2. Either:
           - Known kidney disease history (planned start)
           - No prior history (unplanned/"crash" start) AND survives >90 days
        3. Treatment continues for at least 90 days OR patient:
           - Has planned start and dies within 90 days
           - Transfers to another unit
        4. Patient is modality is assigned to the first 

        ## Timeline Example
        ```
        Key:
        X = Treatment
        - = No Treatment
        * = Death
        T = Transfer

        90 days prior                                                     90 days after
        |<--------------------->|<----------------------->|<----------------->|
        [Gap Check]             [Start]                   [End]           [Follow-up] 
            
        Incident          ------|---XXXXXXXXXXXXXXXXXXXXXX|XXXXX  (Continuous after start)
        Incident          XXX---|---------XXXXXXXXXXXXXXXX|XXXXX  (>90 day gap timeline resets)
        Incident          CKD---|--XXXX*------------------|-----  (Dies within 90 days and prior history)
        Incident          ------|---XXX--XXX-XXXXXXXXX-XXX|XXXXX  (Discontinuous with short gaps)
        Incident          ------|-----------------------XX|XXXXX  (Begins at end of window and continues)
        Incident          ------|---XXXXXXXXXXXXXXXXT-----|-----  (Treatment ends with transfer out)
        
        Not Incident 
        Not Incident      ------|--XXXX*------------------|-----  (Dies within 90 days and no prior history)
        Not incident      XXX---|-XXXXXXXXXXXXXXXXXXXXXXXX|XXXXX  (<90 day gap and timeline begins prior)
        ```

        ## UKRDC Entities Used
        - [PatientRecord](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450149/PatientRecord): ukrdcid, sendingextract
        - [Patient](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450145/Patient): deathtime
        - [Treatment](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450155/Treatment+Encounter): qbl05, hdp04, fromtime, totime, dischargereasoncode, healthcarefacilitycode
        - [ModalityCodes](https://renalregistry.atlassian.net/l/cp/Ac1YeFfH): registry_code_type

        ## Data Quality & Limitations
        The software used to calculate the statistics should be considered experimental and is subject to the following non-exhaustive limitations:
        1. Recent Window Effects (<90 days follow-up):
           - Cannot confirm treatment continuation
           - May count some patients receiving acute treatment

        2. Data Coverage:
           - Inter-unit transfers appear as new starts
           - These may inflate incidence rates

        3. Data Quality:
           - UKRDC contains uncleaned data and may be incomplete or contain errors
        """
    ),
    "PREVALENT_KRT_COHORT": dedent(
        """
        # Prevalent Kidney Replacement Patients

        ## Definition
        A patient receiving ongoing kidney replacement therapy (KRT) - defined as haemodialysis, peritoneal dialysis, 
        or kidney transplant - who is established on treatment at the end of the selected time period.

        ## Inclusion Criteria
        1. Treatment continues beyond end of selected dates
        2. Has received at least 90 days of treatment before window end
        3. No gaps in treatment greater than 90 days
        4. Either:
            - Active treatment at window end
            - Recent transfer to another unit (discharge code 38)

        ## Timeline Example 
        ```
        Example Cases:
        X = Treatment
        - = No Treatment
        * = Death
        T = Transfer
        
                                                        Prevalence Point                                        
        |<-------------------->|<--------------------------->|
                                       [Analysis Window]      

        Prevalent        XXXXXX|XXXXXXXXXXXXXXXXXXXXXXXXXXXXX|X----  (Active at end)
        Prevalent        XXXXXX|XXXXXXXXXXXXXXXXXXXXXXXXT--->|>----  (Transfer out)
        Prevalent        --XXXX|XXXXXXXX------------XXXXXXXXX|X----  (>90 days at end)
        Prevalent        ------|----------------------------X|XXXXX  (>90 days at end)
        
        Not Prevalent    XXXXXX|XXXXXXXXXXXXXXX*-------------|-----  (Died in window)
        Not Prevalent    ------|----------------------XXXX---|-X---  (<90 days total)
        ```    

        ## UKRDC Entities Used
        The chart was produced by joining the following UKRDC entities according to their foreign key relationships:
        - [PatientRecord](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450149/PatientRecord): ukrdcid, sendingextract
        - [Patient](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450145/Patient): deathtime
        - [Treatment](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450155/Treatment+Encounter): qbl05, hdp04, fromtime, totime, dischargereasoncode, healthcarefacilitycode
        - [ModalityCodes](https://renalregistry.atlassian.net/l/cp/Ac1YeFfH): registry_code_type

        ## Data Quality & Limitations
        The software used to calculate the statistics should be considered experimental and is subject to the following non-exhaustive limitations:
        
        1. Data Coverage:
        - Patients from another unit may be counted as prevalent if they are being treated in the unit at prevalence point

        2. Data Quality:
        - UKRDC contains uncleaned data and may be incomplete or contain errors

    """
    ),
    "INCENTRE_DIALYSIS_FREQ": dedent(
        """
        # In-Centre Dialysis Frequency

        ## Overview
        This histogram represents the mean number of dialysis sessions per week for all dialysis patients in a three month period at a sendingfacility or one of its satellites. Optionally the chart can be filtered by satellite unit. 

        ## Methodology
        - Dialysis sessions are counted for patients in the 'All Patients Undergoing Kidney Replacement Therapy' cohort. This is done by grouping on the procedure type code. 
        - Patients with less than two sessions are rejected. 
        - The per week frequency is calculated for each person by dividing the count by the time difference between their first and last dialysis session within the three month period.
        - Patients are aggregated into bins of with boundaries (0.0, 0.5, 1.5, 2.5, 3.5, 7.0). This are labelled 1,2,3 and >3 sessions per week.  

        ## UKRDC Entities Used
        The dialysis sessions table is queried by grouping by ukrdcid with the following aggregate functions used:
        - https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2005565449/Dialysis+Session+Procedure: MIN(fromtime), MAX(totime), COUNT(sessiontype).
        """
    ),
    "INCENTRE_DIALYSIS_TIME": dedent(
        """
        # In-Centre Dialysis Time

        ## Overview
        """
    ),
    "INCIDENT_INITIAL_ACCESS": dedent(
        """
        # Incident Initial Access
        ## Overview
        This pie chart shows the vascular access recorded on the first dialysis session of each incident patient. Optionally the chart can be filtered by satellite unit.

        ## Methodology
        - This cohort is identical to that used for incident patients treatment breakdown.
        - The first session is selected by ranking the dialysis sessions in time.
        - If the type of vascular access has been recorded it is counted accordingly otherwise it is counted as Unknown/Incomplete.
       
       ## UKRDC Entities Used
        - [Dialysis Session](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2005565449/Dialysis+Session+Procedure) qhd20 for first dialysis session. 
        """
    ),
    "PREVALENT_MOST_RECENT_ACCESS": dedent(
        """
        # Prevalent Most Recent Access
        ## Overview
        Vascular access recorded on the most recent dialysis session of each prevalent patient.

        ## Methodology
        - This cohort is identical to that used for prevalent patients treatment breakdown.
        - The most recent coded vascular access is selected within a two week period of the end of the calculation window
        - ipsum lopsum nausium
       
       ## UKRDC Entities Used
        - [Dialysis Session](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2005565449/Dialysis+Session+Procedure) qhd20 for first dialysis session. 
        """
    ),
}

demographic_generic = """
        - The initial cohort is selected by filtering the patientrecords sent via the ukrdc.  
        - The cohort is filtered to only include patients with a treatment record which overlaps 90 days prior to the calculation time. 
"""

careplanning_descriptions = {
    "CKD_CAREPLANNING": dedent(
        """
        # Prevalent CKD Careplanning Assessment

        ## Overview
        This pie chart shows the outcome of the most recent Transition to Long-Term
        (TPLT) careplanning assessment for prevalent CKD patients at the prevalence
        point. Patients with no recorded assessment are shown as "No assessment".

        ## Assessment Outcomes
        - Suitable: Patient assessed as suitable for long-term KRT planning
        - In-progress: Assessment ongoing
        - Unsuitable: Patient assessed as not yet suitable
        - No assessment: No TPLT assessment recorded

        ## Methodology
        - Careplanning data is sourced from the XML archive database
        - Only UKRDC extract patients can be labelled (archive holds UKRDC data only)
        - The most recent assessment overlapping the prevalence point is selected

        ## UKRDC Entities Used
        - [PatientRecord](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450149/PatientRecord): sendingextract
        - XML Archive: Careplanning assessments (assessmenttypecode, assessmentoutcomecode)
        """
    ),
    "KRT_CAREPLANNING": dedent(
        """
        # KRT Careplanning Assessment

        ## Overview
        This pie chart shows the outcome of the most recent Transition to Long-Term
        (TPLT) careplanning assessment for KRT patients. Patients with no recorded
        assessment are shown as "No assessment".

        ## Assessment Outcomes
        - Suitable: Patient assessed as suitable for long-term KRT planning
        - In-progress: Assessment ongoing
        - Unsuitable: Patient assessed as not yet suitable
        - No assessment: No TPLT assessment recorded

        ## Methodology
        - Careplanning data is sourced from the XML archive database
        - Only UKRDC extract patients can be labelled (archive holds UKRDC data only)
        - The most recent assessment overlapping the prevalence point is selected

        ## UKRDC Entities Used
        - [PatientRecord](https://renalregistry.atlassian.net/wiki/spaces/UD/pages/2006450149/PatientRecord): sendingextract
        - XML Archive: Careplanning assessments (assessmenttypecode, assessmentoutcomecode)
        """
    ),
}


demographic_descriptions = {
    "GENDER_DESCRIPTION": dedent(
        f"""
        # Patient Gender
        Gender identity recorded for each living patient registered with the renal unit. Specifically it is defined as the person stated gender code in the nhs data dictionary
        
        # Methodology
        {demographic_generic}
        - Patient records are matched to NHS stated gender using patient demographic information 
        - Patients are optionally checked against NHS tracing to check for date of death
        - All living patients with patient records sent by a particular sending facility are aggregated based on gender


        ## UKRDC Entities Used
        - [PatientRecord](https://renalregistry.atlassian.net/l/cp/KCZ6A2bX)
        - [Patient](https://renalregistry.atlassian.net/l/cp/0MXHtpTU)
        
        """
    ),
    "ETHNIC_GROUP_DESCRIPTION": dedent(
        f"""
        # Patient Ethnicity

        ## Overview 
        Ethnicity group code recorded for each living patient registered with the renal unit over all time.
        The five ethnicity groupings used to map ethnicity codes onto the displayed ethnicity values are the same as those used in the Renal Registry Annual Report.
        
        ## Methodology
        {demographic_generic}
        - Patient records are matched to ethnicity using patient demographic information 
        - Patients are optionally checked against NHS tracing to check for date of death
        - All living patients with patient records sent by a particular sending facility are aggregated based on ethnicity

        ## UKRDC Entities Used
        - [PatientRecord](https://renalregistry.atlassian.net/l/cp/KCZ6A2bX)
        - [Patient](https://renalregistry.atlassian.net/l/cp/0MXHtpTU)
        
        """
    ),
    "AGE_DESCRIPTION": dedent(
        f"""
        # Patient Age
        The age, calculated from date of birth, recorded for each living patient registered with the renal unit.
        
        # Methodology
        {demographic_generic}
        - Patient records are matched to date of birth using patient demographic information 
        - Age is calculated from date of birth 
        - Patients are optionally checked against NHS tracing to check for date of death
        - All living patients with patient records sent by a particular sending facility are aggregated based on age
        
        ## UKRDC Entities Used
        - [PatientRecord](https://renalregistry.atlassian.net/l/cp/KCZ6A2bX)
        - [Patient](https://renalregistry.atlassian.net/l/cp/0MXHtpTU)
        """
    ),
}
