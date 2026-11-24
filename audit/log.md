# Audit Log

Public record of audit report runs of ukrdc-stats. These are processes that will either need non public data or human interpretation. Any specific centres or values recorded here only represent efforts to improve the accuracy of the code and don't represent official statistics. Strictly aggregate results only.

TODO: create a unique id to attach to report runs and output all the input parameters to file with id

## 13-11-26 - Template

**Audit**: `krt_incident` vs raw SQL approximation (`pg_temp.incident_cohort`)

- **Scripts**: `audit/krt_incidence/incidence_self_audit_{quarterly,yearly,steps}.py`
- **SQL**: `audit/sql_raw/approximation_functions.sql`
- **Periods**: <!-- e.g. 2025 Q4 - 2026 Q3 -->
- **Centres**: RAJ, RAQ01, RCSLB, RH8, RNJ00
- **Server**: ukrdc_live

### Findings

### Actions

## 19-11-26

**Audit**: `krt_prevalent` vs annual report 2024

- **Scripts**: `scripts/extract_krt_demog.py`
- **Audit data**: `R:/Annual Reports/ESKD annual reports/CURRENT report/ANNUAL REPORT CONTENT Stats and Indesign/Prevalence/Excel files/Prev_demog_CURRENT.xls"`
- **Periods**: 2024 Q4
- **Centres**: All live
- **Server**: ukrdc_live

### Findings
- A few patients with `centre_code` that doesn't correspond to the `sendingfacility`, particularly for BHLY, possibly due to centres submitting data for other treatment locations. Preferable to filter these out at the calculator stage, perhaps by only allowing particular combinations of `sendingfacility` and `centre_code`.
- The following are percentages of UKRDC/UKRR counts:
#### HD
Derby 200%, Exeter 150%, Reading 125%, Shrewsbury 134%. Derby could be known AKI issue.
#### PD
Stevng 126%, Nottm 132%, Derby 128%, Exeter 156%, Redng 140%, Barts 277%, Doncaster 35%. Could be a similar issue with Barts PD as in version 2.
#### Tx
Bradford 4%, York 6%, Hull 6%, Exeter 186%, Reading 122%, Shrew 155%,Leeds 187%. Possible that BHY transplants are all allocated to Leeds.
#### HHD
Numbers are good considering small. Derby and Reading ~25% over, Shrew and Donc ~40% under.




### Actions
SS to:
- compare against checks done for previous releases
- investigate individual level data for problematic centres.

PM : 
- Have changed the way the treatments are ordered putting preference on most recent from time. This seems to help since as we discovered before there a many short term treatments which are opened but never closed 

TODO:
- Run audit using the prevalence_self_audit and prevalent_compare_row level 
- Review the logic of the centre, satellite mapping and filtering