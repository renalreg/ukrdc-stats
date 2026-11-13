"""Incidence self-audit, one row per rolling year.

Compares krt_incident with the raw sql approximation for the last
NO_OF_YEARS 12 month windows, each ending at the end of END_YEAR Q
END_QUARTER (or a whole number of years before it).
The shared steps live in self_audit_steps.py.

Run from the repo root:
    python audit/krt_incidence/self_audit_yearly.py
"""

import datetime as dt

from self_audit_steps import (
    Period,
    compare,
    load_python_cohort,
    load_sql_cohort,
    report,
)

END_YEAR = 2026
END_QUARTER = 2
NO_OF_YEARS = 3


def years() -> list[Period]:
    """The last NO_OF_YEARS 12 month windows as (start, end) pairs, newest first."""
    # first day after END_QUARTER; Q4 rolls into January of the next year
    end_year = END_YEAR + END_QUARTER // 4
    end_month = 3 * END_QUARTER % 12 + 1
    periods = []
    for i in range(NO_OF_YEARS):
        start = dt.datetime(end_year - i - 1, end_month, 1)
        end = dt.datetime(end_year - i, end_month, 1)
        periods.append((start, end))
    return periods


def main() -> None:
    """Run the yearly self-audit."""
    periods = years()
    sql = load_sql_cohort(periods)
    py = load_python_cohort(periods)
    report(compare(py, sql), name=f"yearly_{END_YEAR}Q{END_QUARTER}")


if __name__ == "__main__":
    main()
