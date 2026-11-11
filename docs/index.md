# ukrdc-stats

`ukrdc-stats` produces statistics from the UKRDC database for dashboards and ad-hoc statistical work. 

## How it fits together

Data flows through three layers. Each layer has one job:

- **[Cohorts](concepts/cohorts.md)** query the UKRDC for a population and return a
  patient-level DataFrame. They validate the result. This is where most audit/validation effort sits.
- **[Labellers](concepts/labellers.md)** add derived columns such as age, eGFR or first-seen date.
  They are reusable, and they can run inside a cohort or on a custom cohort. This is where most development effort sits.
- **[Utilities](concepts/utils.md)** hold generic logic. This includes formulae (such as the eGFR equation),
  code mapping, and aggregation of the cohort down to summary tables.

The **calculators** are a legacy API layer kept for compatibility. See [Legacy calculators](legacy/calculators.md).

## Data shape

- Cohorts are **long format**: one row per patient, or one row per patient per centre by default.
- Labellers make the frame **wider** by adding columns. They do not change the row count.
- Calling a cohort for several centres or periods and concatenating the results makes the frame **longer**.

## Quick start

```python
import datetime as dt

from ukrdc_stats.cohorts.base import ckd_prevalent
from ukrdc_stats.labellers.clinical import link_first_seen

cohort = ckd_prevalent(session, "RXX", dt.datetime(2025, 12, 31))
cohort = link_first_seen(session, cohort)
```

`session` is a UKRDC SQLAlchemy session. See [Utilities](concepts/utils.md) for how to create one.

## Documentation

- [Cohorts](concepts/cohorts.md): what each cohort contains, its grain, and how to build a custom cohort.
- [Labellers](concepts/labellers.md): the labeller contract and the available labellers.
- [Utilities](concepts/utils.md): formulae, aggregation and database helpers.
- [Future work](future.md): placeholders and planned changes.
- [API reference](api/cohorts/base.md): generated from the docstrings.
