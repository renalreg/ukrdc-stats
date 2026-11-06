import datetime as dt

import pandas as pd

from ukrdc_stats.cohorts.base import (
    _chain_treatments,
    _clean_equal_records,
    _label_timeline,
)


def test_clean_equal_records():
    """Example of the kind of duplicated record which results in two
    sendingfacilies sending the same treatment record"""

    infinity = dt.datetime(2200, 1, 1)
    cohort = pd.DataFrame(
        {
            "pid": [100000000, 200000000],
            "ukrdcid": [111111111, 111111111],
            "sendingfacility": ["RFDOG", "RFCAT"],
            "healthcarefacilitycode": ["RFDOG", "RFCAT"],
            "admitreasoncode": ["20", "20"],
            "admitreasoncodestd": ["CF_RR7_TREATMENT", "CF_RR7_TREATMENT"],
            "admissionsourcecode": [None, None],
            "admissionsourcecodestd": [None, None],
            "qbl05": [None, None],
            "hdp04": [None, None],
            "dischargereasoncode": [None, "38"],
            "dischargereasoncodestd": [None, "CF_RR7_DISCHARGE"],
            "dischargelocationcode": [None, "RFDOG"],
            "dischargelocationcodestd": [None, "ODS"],
            "registry_code_type": ["TX", "TX"],
            "end_of_care": ["0", "0"],
            "acute": ["0", "0"],
            "transfer_in": ["0", "0"],
            "deathtime": [infinity, infinity],
            "birthtime": [dt.datetime(1983, 9, 13, 1), dt.datetime(1983, 9, 13, 1)],
            "fromtime": [dt.datetime(2024, 2, 13), dt.datetime(2024, 2, 13)],
            "totime": [infinity, infinity],
            "ckd_centre": [None, "RFCAT"],
            "historic_tx": [False, False],
            "dialtplt": ["TX", "TX"],
            "timeline_order": [0, 1],
            "prev_fromtime": [pd.NaT, dt.datetime(2024, 2, 13)],
            "prev_totime": [pd.NaT, infinity],
            "is_equal": [False, True],
            "timeline_start": [dt.datetime(2024, 2, 13), dt.datetime(2024, 2, 13)],
            "timeline_stop": [infinity, infinity],
        }
    )
    cohort["timeline_length"] = cohort["timeline_stop"] - cohort["timeline_start"]
    cohort["length_of_life"] = cohort["deathtime"] - cohort["timeline_start"]

    cleaned = _clean_equal_records(cohort)

    # only the row matching the ckd centre survives
    assert len(cleaned) == 1
    kept = cleaned.iloc[0]
    assert kept["pid"] == 200000000
    assert kept["sendingfacility"] == "RFCAT"


def test_timeline_with_contained_records():
    """Record A contains records B and C with a >90 day gap between B and C.
    The gap must not reset the timeline because A spans it: the timeline runs
    from the start of A to the end of A."""

    infinity = dt.datetime(2200, 1, 1)
    recovery_window = dt.timedelta(days=90)

    # Hastings (contained records) and Stamford Bridge (genuine gap) cohorts
    cohort = pd.DataFrame(
        {
            "ukrdcid": ["1066", "1066", "1066", "1066b", "1066b"],
            "fromtime": [
                dt.datetime(2024, 1, 1),  # A: spans the whole year
                dt.datetime(2024, 2, 1),  # B: contained by A
                dt.datetime(2024, 8, 1),  # C: contained by A, gapped from B
                dt.datetime(2024, 1, 1),  # D: closed early
                dt.datetime(2024, 8, 1),  # E: genuine >90 day gap from D
            ],
            "totime": [
                dt.datetime(2024, 12, 31),
                dt.datetime(2024, 3, 1),
                dt.datetime(2024, 9, 1),
                dt.datetime(2024, 2, 1),
                infinity,
            ],
            "deathtime": [infinity] * 5,
        }
    )

    chained = _chain_treatments(cohort, recovery_window)
    labelled = _label_timeline(chained)

    # contained records never start a new timeline
    hastings = labelled[labelled["ukrdcid"] == "1066"]
    assert list(hastings["new_timeline"]) == [True, False, False]
    assert (hastings["timeline_start"] == dt.datetime(2024, 1, 1)).all()
    assert (hastings["timeline_stop"] == dt.datetime(2024, 12, 31)).all()
    assert (hastings["timeline_length"] >= dt.timedelta(days=0)).all()

    # a genuine gap resets the timeline to the post-gap record
    stamford = labelled[labelled["ukrdcid"] == "1066b"]
    assert list(stamford["new_timeline"]) == [True, True]
    assert (stamford["timeline_start"] == dt.datetime(2024, 8, 1)).all()
    assert (stamford["timeline_stop"] == infinity).all()