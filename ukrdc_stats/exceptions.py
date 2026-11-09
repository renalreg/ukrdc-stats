"""Exceptions for the UKRDC Statistics API."""


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
