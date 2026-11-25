"""Placeholder tests for krt_prevalent.

Each test is skipped until implemented; the docstring pins the scenario
it must cover. Patching pattern to follow lives in dont_test_synthetic.py
(patch query_krt_prevalent, main_satellite_centres, validate_centre).
"""

import pytest


@pytest.mark.skip(reason="placeholder: not implemented")
def test_hd_with_home_qbl05_relabelled_hhd():
    """HD modality with qbl05 == "HOME" is relabelled HHD; HD elsewhere
    and PD/TX rows are untouched."""


@pytest.mark.skip(reason="placeholder: not implemented")
def test_prevalence_window_filter_and_dedup():
    """Rows with fromtime <= prevalence_point and (totime > point or NaT)
    are kept; treatments starting after the point or ending before it are
    dropped; one row per ukrdcid survives, keeping the latest fromtime."""


@pytest.mark.skip(reason="placeholder: not implemented")
def test_centre_labelling_runs_in_otherise_mode():
    """main_satellite_centres is called with outlier_mapping_mode="otherise"
    and the resulting centre_code/satellite_code columns flow through to
    the returned cohort."""


@pytest.mark.skip(reason="placeholder: not implemented")
def test_transplants_reassigned_to_supervising_centre():
    """_reassign_transplants moves transplant rows to the supervising
    centre so centre_code counts reflect supervision, not the transplanting
    unit."""


@pytest.mark.skip(reason="placeholder: not implemented")
def test_output_validates_against_krt_prevalent_schema():
    """The returned frame satisfies krt_prevalent_schema via the
    @pa.check_output decorator (columns, dtypes, nullability)."""


@pytest.mark.skip(reason="placeholder: not implemented")
def test_empty_cohort_raises():
    """An EmptyCohortError from query_krt_prevalent propagates out of
    krt_prevalent rather than producing an empty report."""
