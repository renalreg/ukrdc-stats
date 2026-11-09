"""Minimal demonstration of pandera synthetic data generation for cohort tests.

Schema.sample() builds a hypothesis strategy from the DataFrameModel and
returns random rows satisfying the schema (dtypes, nullability, isin checks).
Requires hypothesis: pip install hypothesis (or pandera[hypotheses]).

Caveat: sampling produces IID rows. It will not generate realistic
multi-treatment timelines per patient, so this is smoke/invariant coverage
rather than scenario coverage. Directed timeline edge cases still need
hand-built fixtures (see test_incident_functions.py).
"""

import datetime as dt
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("hypothesis")

from ukrdc_stats.cohorts.base import (
    krt_incident,
)
from ukrdc_stats.cohorts.schema import (
    krt_query_incident_schema,
)

CENTRE = "RFRAT"


def _fake_main_satellite_centres(
    _session: object, cohort: pd.DataFrame, **_kwargs: object
) -> pd.DataFrame:
    # skip the FacilityRelationship lookup and map 1:1
    cohort["centre_code"] = cohort["sendingfacility"]
    cohort["satellite_code"] = cohort["healthcarefacilitycode"]
    return cohort


def test_incident() -> None:
    """Run krt_incident end-to-end with the query patched to synthetic data."""
    cohort = krt_query_incident_schema.example(size=50)
    # pin columns that random sampling would otherwise filter out or fail on
    cohort["sendingfacility"] = CENTRE
    cohort["healthcarefacilitycode"] = CENTRE
    cohort["registry_code_type"] = np.resize(["HD", "PD", "TX"], len(cohort))

    # patch names where base looks them up, not where they are defined
    with (
        patch("ukrdc_stats.cohorts.base.query_krt_incident", return_value=cohort),
        patch("ukrdc_stats.cohorts.base.validate_centre"),
        patch(
            "ukrdc_stats.cohorts.base.main_satellite_centres",
            side_effect=_fake_main_satellite_centres,
        ),
    ):
        # @pa.check_output validates the result against krt_incident_schema
        result = krt_incident(
            session=None,
            centre=CENTRE,
            end_date=dt.datetime(2026, 1, 1),
            start_date=dt.datetime(2025, 1, 1),
        )

    assert result["incident"].all()
    assert (result["centre_code"] == CENTRE).all()
    assert not result.duplicated("ukrdcid").any()
