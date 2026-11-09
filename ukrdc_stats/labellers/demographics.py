"""Labellers attaching demographics (age, sex, ethnicity) to a cohort."""

import datetime as dt
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ukrdc_stats.exceptions import MissingColumnError
from ukrdc_stats.labellers.query import query_demog
from ukrdc_stats.utils.data import GENDER_GROUP_MAP, map_codes


def _fetch_missing_demog(
    patient_cohort: pd.DataFrame, column: str, session: Session | None
) -> pd.DataFrame:
    """Fetch demographic data when a required column is absent.

    Looks the data up via query_demog and merges it onto the cohort.

    Args:
        patient_cohort (pd.DataFrame): Cohort being labelled.
        column (str): The column which was found missing.
        session (Session | None): UKRDC session to query demographics with.

    Returns:
        pd.DataFrame: The cohort merged with the missing demographic columns.

    Raises:
        MissingColumnError: If the column is absent and no session is
            available to fetch demographics with.
    """
    if session is None or "pid" not in patient_cohort.columns:
        raise MissingColumnError(f"Patient cohort must contain '{column}' column")

    demog = query_demog(session, patient_cohort["pid"].unique().tolist())
    missing = [
        c for c in demog.columns if c == "pid" or c not in patient_cohort.columns
    ]

    return patient_cohort.merge(demog[missing], on="pid", how="left")


def age(
    patient_cohort: pd.DataFrame,
    reference_date: dt.datetime,
    bins: dict[str, Any] | None = None,
    session: Session | None = None,
) -> pd.DataFrame:
    """Calculate age groups for a patient cohort.

    Args:
        patient_cohort (pd.DataFrame): DataFrame containing patient data with
            a 'birthtime' column.
        reference_date (dt.datetime): Date to calculate ages from.
        bins (dict[str, Any], optional): Dictionary with 'bins' and 'labels'
            keys. Defaults to the standard UKRR age groups.
        session (Session | None, optional): UKRDC session used to fetch
            birthtime when the column is missing. Defaults to None.

    Returns:
        pd.DataFrame: DataFrame with 'decimalage' and 'age' columns added.

    Raises:
        ValueError: If the cohort is already labelled with age.
    """
    if bins is None:
        bins = {
            "bins": [0, 18, 35, 55, 75, 150],
            "labels": ["Under 18", "18-34", "35-54", "55-74", "75+"],
        }

    if "birthtime" not in patient_cohort.columns:
        patient_cohort = _fetch_missing_demog(patient_cohort, "birthtime", session)

    if "age" in patient_cohort.columns:
        raise ValueError("Cohort already labelled with age")

    patient_cohort["decimalage"] = (
        reference_date - patient_cohort["birthtime"]
    ).dt.days / 365.25
    patient_cohort["age"] = pd.cut(
        patient_cohort["decimalage"],
        bins=bins["bins"],
        labels=bins["labels"],
        include_lowest=True,
    )

    return patient_cohort


def sex(
    patient_cohort: pd.DataFrame,
    session: Session | None = None,
) -> pd.DataFrame:
    """Apply gender group mapping to patient cohort.

    Args:
        patient_cohort (pd.DataFrame): DataFrame containing patient data
            with a 'gender' column.
        session (Session | None, optional): UKRDC session used to fetch
            gender when the column is missing. Defaults to None.

    Returns:
        pd.DataFrame: DataFrame with 'sex' column added.
    """
    if "gender" not in patient_cohort.columns:
        patient_cohort = _fetch_missing_demog(patient_cohort, "gender", session)

    patient_cohort["sex"] = (
        patient_cohort["gender"].map(GENDER_GROUP_MAP).fillna("Missing")
    )

    return patient_cohort


def ethnicity(
    patient_cohort: pd.DataFrame,
    session: Session,
) -> pd.DataFrame:
    """Apply UKKA ethnic grouping to patient cohort.

    Args:
        patient_cohort (pd.DataFrame): DataFrame containing patient data
            with an 'ethnicgroupcode' column.
        session (Session): UKRDC session used to look up the code mapping.

    Returns:
        pd.DataFrame: DataFrame with 'ethnicity' column added.
    """
    if "ethnicgroupcode" not in patient_cohort.columns:
        patient_cohort = _fetch_missing_demog(
            patient_cohort, "ethnicgroupcode", session
        )

    ethnic_group_map = map_codes("NHS_DATA_DICTIONARY", "URTS_ETHNIC_GROUPING", session)
    patient_cohort["ethnicity"] = (
        patient_cohort["ethnicgroupcode"].map(ethnic_group_map).fillna("Missing")
    )

    return patient_cohort
