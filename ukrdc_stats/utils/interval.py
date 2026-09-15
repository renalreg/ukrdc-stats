"""
Allen's interval algebra: all 13 relationships between pairs of intervals.

Implemented via a self-join so every record is compared against every other
record within the same group (e.g. ukrdcid), not just its neighbour.
"""

import datetime as dt
from typing import Optional

import pandas as pd


def allen_relationships(
    df: pd.DataFrame,
    group_col: str = "ukrdcid",
    start_col: str = "fromtime",
    end_col: str = "totime",
    recovery_window: Optional[dt.timedelta] = None,
) -> pd.DataFrame:
    """
    Compute all 13 Allen interval relationships for every pair of records
    within each group via a self-join.

    The recovery_window, if provided, is absorbed into the "before"/"after"
    and "overlaps"/"overlapped by" definitions — a gap smaller than the window
    is treated as an overlap rather than a "before".

    Args:
        df: DataFrame with interval records.
        group_col: Column to group records by (e.g. "ukrdcid").
        start_col: Column for interval start time.
        end_col: Column for interval end time.
        recovery_window: Optional gap threshold. Gaps smaller than this are
            treated as overlaps rather than "before"/"after".

    Returns:
        Long-format DataFrame with columns:
        group_col, idx_i, idx_j, start_i, end_i, start_j, end_j, relationship
        where relationship is one of the 13 Allen relations.
    """
    if start_col not in df.columns or end_col not in df.columns:
        raise ValueError(f"Columns {start_col} and {end_col} must be present")

    # Self-join: every pair of records within the same group
    left = df[[group_col, start_col, end_col]].copy()
    right = df[[group_col, start_col, end_col]].copy()

    left["_idx_i"] = df.index
    right["_idx_j"] = df.index

    merged = left.merge(right, on=group_col, suffixes=("_i", "_j"))
    # Exclude self-pairs
    merged = merged[merged["_idx_i"] != merged["_idx_j"]]

    s_i, e_i = merged[f"{start_col}_i"], merged[f"{end_col}_i"]
    s_j, e_j = merged[f"{start_col}_j"], merged[f"{end_col}_j"]

    if recovery_window is not None:
        rw = recovery_window
    else:
        rw = dt.timedelta(0)

    # All 13 Allen relationships
    conditions = [
        # i is entirely before j (with recovery window gap)
        e_i + rw < s_j,
        # i meets j: i ends exactly when j starts
        (e_i == s_j) & (s_i != s_j),
        # i overlaps j: i starts before j, j starts before i ends, i ends before j ends
        (s_i < s_j) & (e_i > s_j) & (e_i < e_j),
        # i is finished by j: i starts before j, same end
        (s_i < s_j) & (e_i == e_j),
        # i contains j: i starts before j, i ends after j
        (s_i < s_j) & (e_i > e_j),
        # i starts j: same start, i ends before j
        (s_i == s_j) & (e_i < e_j),
        # i equals j: same start and end
        (s_i == s_j) & (e_i == e_j),
        # i is started by j: same start, i ends after j
        (s_i == s_j) & (e_i > e_j),
        # i is during j: i starts after j, i ends before j
        (s_i > s_j) & (e_i < e_j),
        # i finishes j: i starts after j, same end
        (s_i > s_j) & (e_i == e_j),
        # i is overlapped by j: j starts before i, i starts before j ends, j ends before i ends
        (s_j < s_i) & (e_j > s_i) & (e_j < e_i),
        # i is met by j: j ends exactly when i starts
        (e_j == s_i) & (s_i != s_j),
        # i is entirely after j (with recovery window gap)
        e_j + rw < s_i,
    ]

    choices = [
        "before",
        "meets",
        "overlaps",
        "finished by",
        "contains",
        "starts",
        "equals",
        "started by",
        "during",
        "finishes",
        "overlapped by",
        "met by",
        "after",
    ]

    import numpy as np

    merged["relationship"] = np.select(conditions, choices, default="unknown")

    result = merged[
        [group_col, "_idx_i", "_idx_j", f"{start_col}_i", f"{end_col}_i", f"{start_col}_j", f"{end_col}_j", "relationship"]
    ].rename(
        columns={
            "_idx_i": "idx_i",
            "_idx_j": "idx_j",
            f"{start_col}_i": "start_i",
            f"{end_col}_i": "end_i",
            f"{start_col}_j": "start_j",
            f"{end_col}_j": "end_j",
        }
    )

    return result.reset_index(drop=True)
