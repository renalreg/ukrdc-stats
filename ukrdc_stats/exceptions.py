"""Exceptions for the UKRDC Statistics API."""

import pandas as pd


class InvalidCentreError(ValueError):
    """Raised when a centre code is not a valid renal centre."""


class NoCohortError(ValueError):
    """Raised when a cohort extraction finds no patients."""


class EmptyCohortError(ValueError):
    """Raised when a cohort query returns no data."""


class NoTestsError(ValueError):
    """Raised when no tests could be run for a requested statistic."""


class MissingColumnError(ValueError):
    """Raised when a dataframe is missing a required column."""


class DataQualityError(ValueError):
    """Raised when raw records violate a schema invariant.

    Args:
        message (str): Description of the violation.
        invalid_rows (pd.DataFrame): The offending records.
    """

    def __init__(self, message: str, invalid_rows: pd.DataFrame) -> None:
        """Store the offending records alongside the message."""
        super().__init__(message)
        self.invalid_rows = invalid_rows
