"""Common utility functions useful in multiple statistics."""

import datetime as dt
import warnings

import pandas as pd
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session
from ukrdc_sqla.ukrdc import Code, CodeMap, Facility, SatelliteMap

from ukrdc_stats.exceptions import MissingColumnError


def egfr(
    scr: float,
    scr_unit: str,
    scr_date: dt.datetime,
    dob: dt.datetime,
    sex: str = "1",
) -> int | None:
    """Calculate the estimated glomerular filtration rate.

    Uses the equation found here: http://nephron.com/epi_equation

    Args:
        scr (float): serum creatinine level
        scr_unit (str): unit of serum creatinine
        scr_date (dt.datetime): date of serum creatinine measurement
        dob (dt.datetime): date of birth
        sex (str, optional): sex of patient as NHS code. Defaults to "1" (male).

    Returns:
        int | None: estimated glomerular filtration rate, or None when the
            patient is under 18 or the inputs are missing
    """
    if pd.isna(scr) or pd.isna(scr_date):
        return None

    age = age_from_dob_exact(scr_date, dob)

    if age < 18:
        return None

    if scr_unit:
        if scr_unit.lower() in ("μmol/l", "umol/l"):
            scr = scr / 88.4
        elif scr_unit.lower() == "mmol/l":
            scr = 1000.0 * scr / 88.4
        elif scr_unit.lower() == "g/l":
            scr = 1000.0 * scr
        else:
            # assume umol/L if unknown unit
            scr = scr / 88.4
    else:
        # assume umol/L if no unit
        scr = scr / 88.4

    if sex == "2":
        kappa = 0.7
        alpha = -0.329
        multiplier = 1.018
    else:
        kappa = 0.9
        alpha = -0.411
        multiplier = 1.0

    scr_frac = scr / kappa
    if scr_frac > 1:
        multiplier = multiplier * (scr_frac**-1.2)
    else:
        multiplier = multiplier * (scr_frac**alpha)

    return round(141 * multiplier * (0.993**age))


def age_from_dob(date: dt.datetime, dob: dt.datetime) -> int:
    """Return the age in whole years on a given date.

    Args:
        date (dt.datetime): Date to calculate age at.
        dob (dt.datetime): Date of birth.

    Returns:
        int: age in years
    """
    years_old: int

    # calculates age by common definition
    years_old = date.year - dob.year
    if (dob.month == 2) & (dob.day == 29):
        # handles case where birthday is on leap day
        year_birthday = dt.datetime(date.year, dob.month, dob.day - 1)
    else:
        year_birthday = dt.datetime(date.year, dob.month, dob.day)
    if year_birthday > date:
        years_old -= 1

    return years_old


def age_from_dob_exact(date: dt.datetime, dob: dt.datetime) -> float:
    """Return the exact age as a decimal fraction of years.

    Args:
        date (dt.datetime): Date to calculate age at.
        dob (dt.datetime): Date of birth.

    Returns:
        float: age in years
    """
    return (date - dob).days / 365.25


def dob_cutoff_from_age(date: dt.datetime, age: int) -> dt.datetime:
    """Returns a date a fixed number of years before the given date.

    Args:
        date (dt.date): date to calculate from
        age (int): number of years before date

    Returns:
        dt.date: date a set number of years ago
    """
    return date - dt.timedelta(days=age * 365.25)


def quarter_to_dates(quarter: int) -> tuple[dt.datetime, dt.datetime]:
    """Return the start and end dates of a ukrdc data quarter.

    Quarters are counted from Q1 1995, the first ukrr data quarter, so
    quarter 1 is Jan-Mar 1995. The end date is the first day of the
    following quarter, giving a [start, end) window.

    Args:
        quarter (int): Quarter number counted from Q1 1995

    Returns:
        tuple[dt.datetime, dt.datetime]: Quarter start and end (exclusive)
    """
    year = 1995 + (quarter - 1) // 4
    quarter_of_year = (quarter - 1) % 4 + 1

    start = dt.datetime(year, 3 * quarter_of_year - 2, 1)
    # the quarter after Q4 starts in January of the next year
    end = dt.datetime(year + quarter_of_year // 4, 3 * quarter_of_year % 12 + 1, 1)

    return start, end


def date_to_quarter(date: dt.datetime) -> int:
    """Return the ukrdc quarter number containing a date.

    Quarters are counted from Q1 1995, the first ukrr data quarter.

    Args:
        date (dt.datetime): Date to map to a quarter

    Returns:
        int: Quarter number counted from Q1 1995

    Raises:
        ValueError: If the date is before Q1 1995
    """
    if date < dt.datetime(1995, 1, 1):
        raise ValueError(f"{date:%Y-%m-%d} predates the first ukrdc quarter (Q1 1995)")

    return (date.year - 1995) * 4 + (date.month - 1) // 3 + 1


def map_codes(
    source_std: str, destination_std: str, session: Session
) -> dict[str, str]:
    """Return a code mapping from the ukrdc code map table as a dictionary.

    Args:
        source_std (str): Coding standard to map from
        destination_std (str): Coding standard to map to
        session (Session): UKRDC database session

    Returns:
        dict[str, str]: Mapping of source codes to destination codes

    Raises:
        ValueError: If no mapping exists between the two coding standards
    """
    query = select(CodeMap.source_code, CodeMap.destination_code).where(
        and_(
            CodeMap.source_coding_standard == source_std,
            CodeMap.destination_coding_standard == destination_std,
        )
    )

    codes = {row.source_code: row.destination_code for row in session.execute(query)}
    if not codes:
        raise ValueError(
            f"No codes found for source coding standard '{source_std}' "
            f"and destination coding standard '{destination_std}'"
        )

    return codes


def lookup_codes(
    coding_standard: str, attribute: str, session: Session
) -> dict[str, str | None]:
    """Return an attribute for every code in a ukrdc coding standard.

    The attribute is most likely the code's description.

    Args:
        coding_standard (str): The coding standard to lookup
        attribute (str): The attribute to return (e.g., 'description')
        session (Session): SQLAlchemy database session

    Returns:
        dict[str, str | None]: Dictionary mapping code values to the requested
            attribute
    """
    # Build and execute query properly
    query = select(Code).where(Code.coding_standard == coding_standard)
    result = session.execute(query).scalars().all()

    # Handle empty results
    if not result:
        warnings.warn(
            f"No codes found for coding standard '{coding_standard}'", stacklevel=2
        )
        return {}

    # Convert to dictionary directly from ORM objects
    return {code.code: getattr(code, attribute, None) for code in result}


def row_completeness(row: pd.Series, groupby_attributes: list[str]) -> int:
    """Calculate completeness based on specified groupby attributes."""
    return int(row[groupby_attributes].notna().sum())


def aggregate_data(
    cohort_wide: pd.DataFrame,
    column_attributes: list[str],
    row_attributes: list[str] | None = None,
) -> pd.DataFrame:
    """Generate long-format headcounts split by the specified columns.

    Headcounts (ukrdcid) are split by the column attributes; the remaining
    columns are rolled into the attribute (column name) and variable
    (column value) columns.

    Args:
        cohort_wide (pd.DataFrame): dataframe containing patient data
        column_attributes (list[str]): Columns to split headcounts by
        row_attributes (list[str], optional): Columns to melt into the
            attribute/variable columns. Defaults to every column which is
            neither a column attribute nor ukrdcid.

    Returns:
        pd.DataFrame: Long format headcounts with attribute, variable and
            count columns
    """
    # catch issues early
    if row_attributes is None:
        row_attributes = [
            col
            for col in cohort_wide.columns
            if col not in column_attributes and col != "ukrdcid"
        ]

    if "ukrdcid" not in cohort_wide.columns:
        raise MissingColumnError("ukrdcid not found in cohort_wide")

    missing_columns = [
        col for col in column_attributes if col not in cohort_wide.columns
    ]
    if missing_columns:
        raise MissingColumnError(
            f"cohort_wide is missing column attributes: {missing_columns}"
        )

    missing_rows = [col for col in row_attributes if col not in cohort_wide.columns]
    if missing_rows:
        raise MissingColumnError(
            f"cohort_wide is missing row attributes: {missing_rows}"
        )

    # drop unneeded columns
    cohort_wide = cohort_wide[column_attributes + row_attributes + ["ukrdcid"]]

    # transform dataframe into long form and count heads
    cohort_long = cohort_wide.melt(
        id_vars=[*column_attributes, "ukrdcid"],
        value_vars=row_attributes,
        var_name="attribute",
        value_name="variable",
    )
    cohort_long = (
        cohort_long.groupby(
            ["attribute", "variable", *column_attributes],
            dropna=False,
        )
        .agg({"ukrdcid": "nunique"})
        .reset_index()
    )

    return cohort_long.rename(columns={"ukrdcid": "count"})


def main_centres(session: Session, when: dt.datetime | None = None) -> list[str]:
    """Select set of main centres with ukrdc rda feed.

    A main centre is a facility which appears as a main unit in the
    satellite map view, started sending data before the quarter containing
    `when`, and had not stopped sending by `when`.
     
    This needs reviewing in light of the rr1(as opposed to rr1+) list which
    seems to be an up-to-date list of main centres. 

    Args:
        session (Session): UKRDC database session
        when (dt.datetime | None): Point in time to test the feed against,
            defaults to now

    Returns:
        list[str]: Sorted list of main centre codes
    """
    when = when or dt.datetime.now()
    when_quarter = date_to_quarter(when)

    # This needs reviewing in light of the rr1(as opposed to rr1+) list which
    # seems to be an up-to-date list of main centres.  
    main_units = select(SatelliteMap.main_unit_code).ext(
        distinct_on(SatelliteMap.main_unit_code)
    )

    query = select(Facility.facilitycode).where(
        Facility.firstdataquarter < when_quarter,
        or_(Facility.enddate.is_(None), Facility.enddate > when),
        Facility.facilitycode.in_(main_units),
    )

    centres = session.execute(query).all()
    return sorted(row[0] for row in centres)


# NHS digital gender map
GENDER_GROUP_MAP = {"1": "Male", "2": "Female", "9": "Indeterminate", "X": "Unknown"}


AGE_BINS = {
    "labels": ["<18", "18-34", "35-54", "55-74", ">=75"],
    "bins": [0, 18, 35, 55, 75, 150],
}
