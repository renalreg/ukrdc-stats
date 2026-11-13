"""Incidence self-audit, one row per quarter.

Compares krt_incident with the raw sql approximation for the last
NO_OF_QUARTERS quarters, up to and including END_YEAR Q END_QUARTER.
The shared steps live in self_audit_steps.py.

Run from the repo root:
    python audit/krt_incidence/self_audit_quarterly.py
"""

import datetime as dt

from incidence_self_audit_steps import (
    Period,
    compare,
    load_python_cohort,
    load_sql_cohort,
    report,
)

END_YEAR = 2026
END_QUARTER = 3
NO_OF_QUARTERS = 4


def quarters() -> list[Period]:
    """The last NO_OF_QUARTERS quarters as (start, end) pairs, newest first."""
    periods = []
    year, quarter = END_YEAR, END_QUARTER
    for _ in range(NO_OF_QUARTERS):
        start = dt.datetime(year, 3 * quarter - 2, 1)
        # the quarter after Q4 starts in January of the next year
        end = dt.datetime(year + quarter // 4, 3 * quarter % 12 + 1, 1)
        periods.append((start, end))
        year, quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    return periods


def main() -> None:
    """Run the quarterly self-audit."""
    periods = quarters()
    sql = load_sql_cohort(periods)
    py = load_python_cohort(periods)
    report(compare(py, sql), name=f"quarterly_{END_YEAR}Q{END_QUARTER}")


if __name__ == "__main__":
    main()
