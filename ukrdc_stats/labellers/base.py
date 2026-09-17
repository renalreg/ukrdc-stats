import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ukrdc_sqla.ukrdc import PatientNumber


def nhs_number(
    patient_cohort: pd.DataFrame,
    session: Session,
    include_type: bool = False,
) -> pd.DataFrame:
    """
    Attaches the NHS number (or CHI/HSC equivalent) to each patient in the
    cohort by querying the PatientNumber table. Where an NHS number is not
    available, falls back to CHI then HSC, matching the pattern used in
    incident_patients_year_comparison.py.

    Args:
        patient_cohort: DataFrame with a ``pid`` column.
        session: UKRDC database session.
        include_type: If True, also add a ``nhs_number_type`` column
            containing ``"NHS"``, ``"CHI"`` or ``"HSC"`` to indicate which
            identifier was used.

    Returns:
        The cohort with ``nhs_number`` (and optionally ``nhs_number_type``)
        columns merged on ``pid``.
    """
    if "nhs_number" in patient_cohort.columns:
        raise ValueError("Cohort already labelled with nhs_number")
    
    

    pids = patient_cohort["pid"].unique().tolist()

    # Query PatientNumber in chunks of 100 to avoid large IN clauses
    chunk_size = 100
    rows = []
    for i in range(0, len(pids), chunk_size):
        chunk = pids[i : i + chunk_size]
        query = (
            select(
                PatientNumber.pid,
                PatientNumber.patientid.label("nhs_number"),
                PatientNumber.organization.label("nhs_number_type"),
            )
            .where(
                PatientNumber.pid.in_(chunk),
                PatientNumber.organization.in_(["NHS", "CHI", "HSC"]),
            )
        )
        rows.extend(session.execute(query).all())

    if not rows:
        patient_cohort["nhs_number"] = pd.NA
        if include_type:
            patient_cohort["nhs_number_type"] = pd.NA
        return patient_cohort

    ni_map = pd.DataFrame(rows, columns=["pid", "nhs_number", "nhs_number_type"])

    # Deduplicate: prefer NHS > CHI > HSC by sorting and keeping first per pid
    priority = {"NHS": 0, "CHI": 1, "HSC": 2}
    ni_map["priority"] = ni_map["nhs_number_type"].map(priority)
    ni_map = (
        ni_map.sort_values("priority")
        .drop_duplicates("pid", keep="first")
        .drop(columns="priority")
    )

    merge_cols = ["pid", "nhs_number"]
    if include_type:
        merge_cols.append("nhs_number_type")

    patient_cohort = patient_cohort.merge(
        ni_map[merge_cols], on="pid", how="left"
    )

    return patient_cohort
