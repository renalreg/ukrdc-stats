# Cohorts

A cohort is a patient-level DataFrame for one population at one point in time.
Cohort functions are the only layer that queries the UKRDC for the population itself.
Labellers and utilities then build on the result.

## Available cohorts

| Function | Population | Grain |
| --- | --- | --- |
| `ckd_prevalent` | Patients with CKD at a prevalence point | One row per patient (schema-enforced) |
| `krt_prevalent` | Patients on kidney replacement therapy (KRT) at a prevalence point, across a centre and its satellites | One row per patient per centre (intended) |
| `krt_incident` | Patients starting KRT for a centre, ending at `end_date` | One row per patient (schema-enforced; see [Future work](../future.md)) |
| `ckd_incident` | Not implemented | See [Future work](../future.md) |

The API reference lists every parameter: [cohorts.base](../api/cohorts/base.md).

## How a cohort is built

1. **Base query.** `cohorts/query.py` returns raw rows from the UKRDC. Each query has a pandera schema in `cohorts/schema.py`.
   The schema is the contract for the base data.
2. **Labelling.** The cohort function calls labellers for columns it needs. For example, `ckd_prevalent` adds age
   and eGFR. These are the only labellers a cohort should run. Other derived columns belong in the caller's code.
3. **Validation.** `validate_centre` in `validation/validate.py` checks that the centre is a valid renal centre.
   The output is checked against the cohort's schema before it is returned.

## Grain

- **Default (intended):** one row per patient per centre.
- **Record-level option:** some cohorts can return one row per patient per centre per record,
  for example each treatment record.
- **De-duplication across centres:** where a cohort keeps one record per patient, that choice is made across all centres.
  If a patient's first treatment was at another centre, they do not appear for the centre being reported.

## Combining cohorts

Call a cohort for each centre or period and concatenate the results with `pd.concat`.
This gives a long-format frame. Pass the result to labellers or aggregation.

## Schemas and data quality

Schemas are strict about types and keys. Some fields are nullable or lenient
because the source data is incomplete. The reason for each relaxation is given in the schema docstring.
Relax a check only with a documented reason.

## Custom cohorts

Ad hoc studies often need a cohort that a standard function does not return.
Build one by taking a base cohort, labelling it with the properties you need, and filtering:

```python
import datetime as dt

from ukrdc_stats.cohorts.base import ckd_prevalent
from ukrdc_stats.labellers.clinical import link_first_seen

prevalence_point = dt.datetime(2025, 12, 31)
cohort = ckd_prevalent(session, "RXX", prevalence_point)  # adds decimalage
cohort = link_first_seen(session, cohort)  # adds first_seen
study = cohort[(cohort["decimalage"] >= 65) & (cohort["first_seen"] <= prevalence_point)]
```

The filter drops patients with no `first_seen` date, because a missing date fails the comparison.
