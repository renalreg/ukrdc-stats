"""Sender-matched archive correction for the CKD cohort.

The CKD cohort is keyed on treating location, so its rows can be sent by
facilities other than the centre being queried. pid is only unique per
(nationalid, sendingfacility), so archive rows must map back to the pid of
the same sender: an archive row sent by one facility (including the centre
itself) must never correct a cohort row sent by another.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

import pandas as pd

from ukrdc_stats.cohorts.query import query_ckd

PREVALENCE_POINT = dt.datetime(2025, 6, 30)

CENTRE = "RFOX"  # treating centre the cohort is queried for


def _ukrdc_cohort() -> pd.DataFrame:
    """The same treatment at RFOX recorded three times: sent by RCAT and
    RDOG (other facilities submitting data for RFOX sites) and by RFOX
    itself, all with the generic 900 code."""
    return pd.DataFrame(
        {
            "pid": ["1", "2", "3"],
            "ukrdcid": ["U1", "U1", "U1"],
            "sendingfacility": ["RCAT", "RDOG", "RFOX"],
            "sendingextract": ["UKRDC"] * 3,
            "birthtime": [dt.datetime(1980, 1, 1)] * 3,
            "deathtime": [None] * 3,
            "healthcarefacilitycode": [CENTRE] * 3,
            "healthcarefacilitydesc": [None] * 3,
            "admitreasoncode": ["900"] * 3,
            "admitreasoncodestd": ["UKKID"] * 3,
            "admitreasondesc": ["CKD"] * 3,
            "fromtime": [dt.datetime(2024, 1, 1)] * 3,
            "totime": [dt.datetime(2025, 1, 1)] * 3,
            "sex": ["1"] * 3,
            "ethnicgroupcode": [None] * 3,
            "ethnicgroupdesc": [None] * 3,
            "ukkaethnicity": [None] * 3,
            "registry_code_type": ["CK"] * 3,
            "lab_egfr_min": [None] * 3,
            "max_creatinine": [None] * 3,
            "max_creatinine_units": [None] * 3,
            "max_creatinine_date": [None] * 3,
        }
    )


def _pid_ni_map() -> pd.DataFrame:
    """Each sender's pid maps to the same national id."""
    return pd.DataFrame(
        {
            "pid": ["1", "2", "3"],
            "sendingfacility": ["RCAT", "RDOG", "RFOX"],
            "patientid": ["NHSNUM"] * 3,
            "organization": ["NHS"] * 3,
        }
    )


def _archive_rows() -> pd.DataFrame:
    """Archive treatments for the same times, all coded 902: RDOG's, the
    centre's own (RFOX), and one from a sender outside the cohort."""
    return pd.DataFrame(
        {
            "sendingfacility": ["RDOG", "RFOX", "RBAT"],
            "patientid": ["NHSNUM"] * 3,
            "organization": ["NHS"] * 3,
            "numbertype": ["NHS"] * 3,
            "admitreasoncode": ["902"] * 3,
            "admitreasoncodestd": ["UKKID"] * 3,
            "admitreasondesc": ["CKD"] * 3,
            "fromtime": [dt.datetime(2024, 1, 1)] * 3,
            "totime": [dt.datetime(2025, 1, 1)] * 3,
        }
    )


def test_correction_is_sender_matched():
    """The centre (RFOX) differs from the sending facilities. RDOG's and
    RFOX's own archive rows correct only their same-sender records; the
    centre's archive row must not leak onto the RCAT-sent record, and rows
    from senders outside the cohort are dropped by the pid map."""

    captured = {}

    def fake_archive_query(_archive_session, facilities, _prevalence_point):
        # mimic the SQL scoping of query_ckd_treatment_archive
        captured["facilities"] = facilities
        rows = _archive_rows()
        return rows[rows["sendingfacility"].isin(facilities)].reset_index(drop=True)

    with (
        patch(
            "ukrdc_stats.cohorts.query.query_ckd_ukrdc",
            return_value=_ukrdc_cohort(),
        ),
        patch("ukrdc_stats.cohorts.query.pid_ni_map", return_value=_pid_ni_map()),
        patch(
            "ukrdc_stats.cohorts.query.get_archive_sessionmaker",
            return_value=MagicMock(),
        ),
        patch(
            "ukrdc_stats.cohorts.query.query_ckd_treatment_archive",
            side_effect=fake_archive_query,
        ),
    ):
        result = query_ckd(
            session=None,
            facility_code=CENTRE,
            prevalence_point=PREVALENCE_POINT,
        )

    # every sendingfacility present in the cohort is sent to the archive,
    # including the centre itself
    assert set(captured["facilities"]) == {"RCAT", "RDOG", "RFOX"}

    corrected = result.set_index("pid")
    # RDOG-sent record corrected by its own sender's archive row
    assert corrected.loc["2", "admitreasoncode"] == "902"
    # centre-sent record corrected by the centre's own archive row
    assert corrected.loc["3", "admitreasoncode"] == "902"
    # RCAT-sent record untouched: the centre's archive row must not
    # correct another sender's record
    assert corrected.loc["1", "admitreasoncode"] == "900"
