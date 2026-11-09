"""Shared query helpers: pid to national identifier mapping."""

import pandas as pd
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from ukrdc_sqla.ukrdc import FacilityRelationship, PatientNumber, PatientRecord
from ukrdc_sqla.utils.constants import RelationshipType

from ukrdc_stats.exceptions import EmptyCohortError


def pid_ni_map(session: Session, centres: list[str]) -> pd.DataFrame:
    """Map patient ids to national ids for a given facility.

    TODO: think through the ambiguities resulting from multiple pids
    mapping to the same ni.
    """
    # append depricated
    depricated_centres = session.execute(
        select(FacilityRelationship.parentfacilitycode).where(
            and_(
                FacilityRelationship.childfacilitycode.in_(centres),
                FacilityRelationship.relationshiptype
                == RelationshipType.deprecated_current,
            )
        )
    ).all()

    centres = centres + [row[0] for row in depricated_centres]

    # get ni
    query = (
        select(
            PatientNumber.pid,
            PatientRecord.sendingfacility,
            PatientNumber.patientid,
            PatientNumber.organization,
        )
        .distinct(
            PatientNumber.pid, PatientNumber.patientid, PatientNumber.organization
        )
        .join(PatientRecord, PatientNumber.pid == PatientRecord.pid)
        .outerjoin(
            FacilityRelationship,
            and_(
                PatientRecord.sendingfacility
                == FacilityRelationship.parentfacilitycode,
                FacilityRelationship.relationshiptype == RelationshipType.feedshare,
            ),
        )
        .where(
            or_(
                PatientRecord.sendingfacility.in_(centres),
                FacilityRelationship.childfacilitycode.in_(centres),
            )
        )
    )
    pid_map = pd.DataFrame(session.execute(query).all())

    if pid_map.empty:
        raise EmptyCohortError("Couldn't create PID to NI mapping")

    return pid_map
