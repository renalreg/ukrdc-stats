"""
Exceptions for the UKRDC Statistics API
"""


class InvalidCentreError(ValueError):
    pass


class NoCohortError(ValueError):
    pass


class EmptyCohortError(ValueError):
    """Raised when a cohort query returns no data"""

    pass


class NoTestsError(ValueError):
    pass


class MissingColumnError(ValueError):
    pass
