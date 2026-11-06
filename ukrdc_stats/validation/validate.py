from ukrdc_sqla.ukrdc import Facility
from ukrdc_sqla.utils.constants import FacilityType
from ukrdc_stats.exceptions import InvalidCentreError
from sqlalchemy import select
from sqlalchemy.orm import Session


def validate_centre(session: Session, centre: str) -> None:
    """
    As the data model for facilities spirals in complexity the ukrdc_stats will
    only support cohort extraction for adult an paediatric renal centres. For
    now this will only be a lookup against the facilities table but in the
    future it should check things like the first data quarter.
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
