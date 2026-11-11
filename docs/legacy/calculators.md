# Legacy calculators

!!! warning "Legacy"
    The `calculators` package is kept for API compatibility with `ukrdc-fastapi`.
    Do not add new functionality here. New work uses [cohorts](../concepts/cohorts.md),
    [labellers](../concepts/labellers.md) and [utilities](../concepts/utils.md).

## Contents

- `KRTStatsCalculator` (`calculators/krt.py`): KRT demographics and careplanning.
- `PrevalentCKDCalculator` (`calculators/ckd.py`): CKD demographics and careplanning.
- `models` (`calculators/models.py`): Pydantic response models.
- `builders` (`calculators/builders.py`): plotting-friendly table builders.

## Migrating

- Build the cohort with `krt_prevalent`, `krt_incident` or `ckd_prevalent`.
- Add columns with labellers.
- Summarise with `aggregate_data`.

For `ukrdc-fastapi` migration steps, see `FASTAPI_MIGRATION.md` in the repository root.
