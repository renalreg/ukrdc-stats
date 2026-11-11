# Labellers

Labellers add derived columns to a patient-level DataFrame.
They are reusable. The same labeller can run on a standard cohort, on a custom cohort, or inside a cohort function.

## Contract

- **Input:** a DataFrame with a `pid` column, plus other inputs only where needed, such as a `session`
  or a `prevalence_point`.
- **Output:** the same DataFrame with new columns added. The number of rows should not change.
- **Named columns:** each labeller adds fixed, named columns. Its docstring lists them.
- **No overwriting:** a labeller raises an error if its columns already exist, as `nhs_number` and `age` do.

The current labellers do not share one signature. Examples:

- `age(patient_cohort, reference_date)`
- `nhs_number(patient_cohort, session, include_type)`
- `egfr(session, patient_cohort, prevalence_point)`

New labellers should take the cohort first, then only the inputs they need as keyword arguments.
Existing labellers will move to this pattern over time. See [Future work](../future.md).

## Available labellers

| Module | Labellers |
| --- | --- |
| `base` | `nhs_number`, `nhs_uid` |
| `demographics` | `age`, `sex`, `ethnicity` |
| `geography` | `imd`, `main_satellite_centres`, `adult_paed` |
| `clinical` | `prevalent_careplanning`, `pre_start_careplanning`, `link_first_seen`, `vascular_access`, `hd_dialysis_frequency` |
| `biomarkers` | `egfr` |
| `data_quality` | See the [API reference](../api/labellers/data_quality.md) |

The full parameter lists are in the [API reference](../api/labellers/base.md).

## eGFR: a labeller that uses a utility

eGFR is both a formula and a column:

- The equation lives in `utils.data.egfr`. It takes a creatinine value, unit, date, date of birth and sex, and returns a number.
- The `egfr` labeller in `labellers.biomarkers` applies that formula to each row and adds the column.

Keep the two apart. A change to the equation belongs in `utils`. A change to which rows get an eGFR belongs in the labeller.

## Labellers inside cohorts

Some cohorts use a labeller to filter as well as to add columns. For example, the CKD cohort
uses eGFR to censor patients. A labeller can add a property, such as `first_seen`,
and the caller can then filter on it. See [Custom cohorts](cohorts.md#custom-cohorts).
