# Utilities

Utilities are generic functions with no study-specific logic. They sit beneath cohorts and labellers.
Use them directly or through a labeller.

## Formulae

`ukrdc_stats.utils.data` holds the calculations that labellers apply to rows:

- `egfr`: the CKD-EPI equation. See [Labellers](labellers.md#egfr-a-labeller-that-uses-a-utility).
- `age_from_dob`, `age_from_dob_exact`: age in whole or decimal years.
- `dob_cutoff_from_age`: the date of birth that gives a given age on a date.

## Code mapping

- `map_codes`: a code map from the UKRDC code map table, as a dictionary.
- `lookup_codes`: an attribute for every code in a coding standard.

## Aggregation

- `aggregate_data`: groups a long-format cohort by `column_attributes`, and optionally by `row_attributes`,
  to give summary counts. Use it to turn a cohort into a dashboard table.
- `row_completeness`: counts how many grouping attributes are present in a row.

## Intervals

`ukrdc_stats.utils.interval.allen_relationships` classifies how pairs of date intervals overlap,
using Allen's interval relations. Use it for treatment timelines.

## Database and caching

- `load_ukrdc_url_from_config`: reads the UKRDC connection URL from a JSON config file.
- `get_sessionmaker`: builds a session factory for a server. Pass `caching=True` to cache query results.
- `get_archive_sessionmaker`: builds a session factory for the XML archive on the same cluster as a session.
- `ukrdc_stats.utils.cache`: the caching layer used by `get_sessionmaker`. Cache keys include the query and its parameters.

Credentials are read from a config file. Do not put them in code or in committed files.

## Query helpers

- `pid_ni_map`: maps patient IDs to national IDs for a list of centres.
