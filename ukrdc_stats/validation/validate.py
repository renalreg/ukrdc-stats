"""Runtime validation of centre codes against the facilities table."""

from sqlalchemy import select
from sqlalchemy.orm import Session
from ukrdc_sqla.ukrdc import Facility
from ukrdc_sqla.utils.constants import FacilityType

from ukrdc_stats.exceptions import InvalidCentreError


def validate_centre(session: Session, centre: str) -> None:
    """Check that the centre is an adult or paediatric renal centre.

    As the data model for facilities spirals in complexity the ukrdc_stats
    will only support cohort extraction for adult and paediatric renal
    centres. For now this is only a lookup against the facilities table but
    in the future it should check things like the first data quarter.

    Args:
        session (Session): UKRDC database session.
        centre (str): Centre code to validate.

    Raises:
        InvalidCentreError: If the centre is not an Adult or Paediatric
            Renal Centre.
    """
    query = select(Facility.facilitytype).where(
        Facility.facilitycode == centre,
        Facility.facilitytype.in_(
            [FacilityType.adult_renal_centre, FacilityType.paediatric_renal_centre]
        ),
    )

    centre_type = session.execute(query).first()
    if not centre_type:
        raise InvalidCentreError(
            f"Centre {centre} is not an Adult or Paediatric Renal Centre"
        )
