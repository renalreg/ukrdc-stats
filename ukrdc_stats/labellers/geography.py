import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import select

from ukrdc_stats.labellers.query import (
    query_postcodes,
    query_paed_centres,
    query_ons_postcode_data,
)
from ukrdc_sqla.ukrdc import FacilityRelationship
from ukrdc_stats.exceptions import MissingColumnError


def imd(session: Session, patient_cohort: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate the Index of Multiple Deprivation (IMD) for each patient.
    """

    # query patient postcodes
    postcodes = query_postcodes(session, patient_cohort["pid"].tolist())
    postcodes["postcode_norm"] = (
        postcodes["postcode"]
        .astype("string")
        .str.replace(" ", "", regex=False)
        .str.upper()
    )

    # query imd from ons
    imd_data = query_ons_postcode_data()
    imd_data["pcd7_norm"] = (
        imd_data["pcd7"].astype("string").str.replace(" ", "", regex=False).str.upper()
    )
    postcodes = postcodes.merge(
        imd_data,
        left_on="postcode_norm",
        right_on="pcd7_norm",
        how="left",
    )
    del imd_data
    postcodes.drop(columns=["postcode_norm", "pcd7_norm", "pcd7"], inplace=True)

    patient_cohort = patient_cohort.merge(
        postcodes[["pid", "imddecile"]],
        left_on="pid",
        right_on="pid",
        how="left",
    )

    return patient_cohort


def main_satellite_centres(
    session: Session,
    patient_cohort: pd.DataFrame,
    outlier_mapping_mode: str = "passthrough",
    drop_outliers: bool = False,
) -> pd.DataFrame:
    """
    This function does the heavy lifting of transforming sending facilities and
    healthcarefacility codes into centre codes and satellite codes. It takes
    the unaggregated data as the input. The best way to do this still needs
    debating in the near term we will implement the following behaviour:

    1. For site sharing a feed the sendingfacility (centre_code) will be
    replaced with the main centre code by mapping back from the satellite code
    2. For sites where the healthcarefacility is a satellite of the sendingfacility
    there will be no change.
    3. For sites where the healthcarefacility is not a satellite both codes will be
    replaced with the sendingfacility.
    4. For feedshare sites there will be way of determining which of the sites
    is a main unit so they will be dropped from the dataframe.


    """

    for column in ("sendingfacility", "healthcarefacilitycode"):
        if column not in patient_cohort.columns:
            raise MissingColumnError(f"patient_cohort is missing column '{column}'")

    facility_relationships = pd.read_sql(select(FacilityRelationship), session.bind)

    feedshare_main_unit_map = facility_relationships[
        facility_relationships["relationshiptype"] == "FEED-SHARE"
    ][["parentfacilitycode", "childfacilitycode"]].drop_duplicates()

    # map main unit codes for feed sharing main units
    patient_cohort = patient_cohort.merge(
        feedshare_main_unit_map,
        left_on=["healthcarefacilitycode", "sendingfacility"],
        right_on=["childfacilitycode", "parentfacilitycode"],
        how="left",
    )
    patient_cohort["centre_code_mapped"] = patient_cohort["childfacilitycode"]
    patient_cohort.drop(
        columns=["parentfacilitycode", "childfacilitycode"], inplace=True
    )

    # feed share satellites
    feedshare_satellite_mapping = feedshare_main_unit_map.merge(
        facility_relationships[
            facility_relationships.relationshiptype == "MAIN-SATELLITE"
        ][["parentfacilitycode", "childfacilitycode"]],
        left_on="childfacilitycode",
        right_on="parentfacilitycode",
        how="left",
        suffixes=("", "_satellite"),
    )

    patient_cohort = patient_cohort.merge(
        feedshare_satellite_mapping,
        left_on=["healthcarefacilitycode", "sendingfacility"],
        right_on=["childfacilitycode_satellite", "parentfacilitycode"],
        how="left",
    )
    patient_cohort.loc[
        patient_cohort["childfacilitycode_satellite"].notna(), "centre_code_mapped"
    ] = patient_cohort.loc[
        patient_cohort["childfacilitycode_satellite"].notna(), "childfacilitycode"
    ]

    # drop unassigned feedshare satellites as there isn't a sensible default
    unassigned = (
        patient_cohort["sendingfacility"].isin(
            feedshare_main_unit_map["parentfacilitycode"].unique()
        )
        & patient_cohort["centre_code_mapped"].isna()
    )
    patient_cohort = patient_cohort[~unassigned]
    patient_cohort.drop(
        columns=[
            "childfacilitycode_satellite",
            "parentfacilitycode_satellite",
            "parentfacilitycode",
            "childfacilitycode",
        ],
        inplace=True,
    )

    # map non-feedshare main units
    patient_cohort.loc[
        patient_cohort.healthcarefacilitycode == patient_cohort.sendingfacility,
        "centre_code_mapped",
    ] = patient_cohort.loc[
        patient_cohort.healthcarefacilitycode == patient_cohort.sendingfacility,
        "sendingfacility",
    ]

    # Map non feed share satellites - this will map satellites to their main unit regardless of sendingfacility
    satellite_map = facility_relationships[
        facility_relationships["relationshiptype"] == "MAIN-SATELLITE"
    ][["parentfacilitycode", "childfacilitycode"]]
    patient_cohort = patient_cohort.merge(
        satellite_map,
        left_on=["healthcarefacilitycode", "sendingfacility"],
        right_on=["childfacilitycode", "parentfacilitycode"],
        how="left",
        suffixes=("", "_satellite"),
    )
    patient_cohort.loc[
        patient_cohort.parentfacilitycode.notna(), "centre_code_mapped"
    ] = patient_cohort.loc[
        patient_cohort.parentfacilitycode.notna(), "parentfacilitycode"
    ]

    # Pass through "other"
    other_mask = patient_cohort["healthcarefacilitycode"].isin(["990", "995", "999"])
    patient_cohort.loc[other_mask, "centre_code_mapped"] = patient_cohort.loc[
        other_mask, "sendingfacility"
    ]

    # Optional behaviour to map any remaining satellites to their main unit code
    if outlier_mapping_mode == "main-centre":
        patient_cohort.loc[
            patient_cohort["centre_code_mapped"].isna(), "healthcarefacilitycode"
        ] = patient_cohort.loc[
            patient_cohort["centre_code_mapped"].isna(), "main_unit_code"
        ]
    elif outlier_mapping_mode == "otherise":
        patient_cohort.loc[
            patient_cohort["centre_code_mapped"].isna(), "healthcarefacilitycode"
        ] = "995"
    elif outlier_mapping_mode == "passthrough":
        pass
    else:
        raise ValueError(f"Invalid outlier_mapping_mode: {outlier_mapping_mode}")

    if drop_outliers:
        patient_cohort = patient_cohort[patient_cohort["centre_code_mapped"].notna()]

    # create output columns
    patient_cohort["centre_code"] = patient_cohort["centre_code_mapped"]
    patient_cohort["satellite_code"] = patient_cohort["healthcarefacilitycode"]

    # delete helper columns
    patient_cohort = patient_cohort.drop(
        columns=["parentfacilitycode", "childfacilitycode", "centre_code_mapped"]
    )

    return patient_cohort


def adult_paed(session: Session, patient_cohort: pd.DataFrame) -> pd.DataFrame:
    """
    Flag if centre is a paediatric centre. For any paed centre that are mapped
    via the main-satellite model recode paed as main centre.
    """

    for column in ("centre_code", "satellite_code"):
        if column not in patient_cohort.columns:
            raise MissingColumnError(f"patient_cohort is missing column '{column}'")

    paed_centres = query_paed_centres(session)

    # Recode paed satellites as main centres
    paed_satellites = patient_cohort["satellite_code"].isin(paed_centres)
    patient_cohort.loc[paed_satellites, "centre_code"] = patient_cohort.loc[
        paed_satellites, "satellite_code"
    ]

    patient_cohort["adult_paed"] = "Adult"
    patient_cohort.loc[
        patient_cohort["centre_code"].isin(paed_centres), "adult_paed"
    ] = "Paed"

    return patient_cohort
