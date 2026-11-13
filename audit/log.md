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
 