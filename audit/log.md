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

## 23-11-26

**Audit**: CKD checks (previous data vs current)

- **Scripts**: `scripts/extract_ckd_demog.py`
- **Audit data**: `K:\Internal work\Portal\UKRDC\CKD/Checks 2026 11 23.xls"`
- **Periods**: 2024 Q3 - 2026 Q2
- **Centres**: All live
- **Server**: ukrdc_live

### Findings
- Comparison of aggregate numbers between run shows new CKD calculator is over counting
- Some centres (BHLY and RAJ) are not outputing the full range of ckd code and defaulting to fallback 900 code

### Actions
WM to:
- Run unaggregated CKD file and look at GFR values in the latest output. Are these consistent with >15 as we would expect?
PM to:
- Check possible reasons for discrepancies in the calculators.

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

pm:
- At least some of these observations are a result of something like the following pattern. A treatment record is opened but the patient is not treated or not treated for long. Subsequently the record is never closed.

- Have changed the way the treatments are ordered putting preference on most recent fromtime to make code more robust against this type of pattern. This may have side effects for patients that recieve small amounts of hd shortly after transplantation but whose modality should properly be recorded as TX.

### Actions
SS to:
- compare against checks done for previous releases
- investigate individual level data for problematic centres.

TODO:
- Run audit using the prevalence_self_audit and prevalent_compare_row level
- Review the logic of the centre, satellite mapping and filtering
