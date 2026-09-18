"""
Version 3.0.0 of dashboard stats introduces pydantic style schemas the
dataframes which are integral to the dashboard stats library. The purpose of
this change is to introduce better type checking and try and eliminate some of
the errors which arise from dataframes not having the expected columns.

In the first instance the schema will just look a type hinting and validating
which columns can be null but pandera provides many much more sophisticated
options here.
"""

import pandera.pandas as pa
from pandera.typing import Series


class krt_base_schema(pa.DataFrameModel):
    # Core columns from base cohort query
    pid: Series[str]
    ukrdcid: Series[str]
    sendingfacility: Series[str]
    sendingextract: Series[str]
    healthcarefacilitycode: Series[str] = pa.Field(nullable=True)
    admitreasoncode: Series[str]
    admitreasoncodestd: Series[str]
    admissionsourcecode: Series[str] = pa.Field(nullable=True)
    admissionsourcecodestd: Series[str] = pa.Field(nullable=True)
    registry_code_type: Series[str]
    qbl05: Series[str] = pa.Field(nullable=True)
    hdp04: Series[str] = pa.Field(nullable=True)
    deathtime: Series[pa.DateTime] = pa.Field(nullable=True)
    fromtime: Series[pa.DateTime]
    totime: Series[pa.DateTime] = pa.Field(nullable=True)

    class Config:
        coerce = True


class krt_query_prevalent_schema(krt_base_schema):
    pass


class krt_query_incident_schema(krt_base_schema):
    dischargereasoncode: Series[str] = pa.Field(nullable=True)
    dischargereasoncodestd: Series[str] = pa.Field(nullable=True)
    dischargelocationcode: Series[str] = pa.Field(nullable=True)
    dischargelocationcodestd: Series[str] = pa.Field(nullable=True)
    end_of_care: Series[str] = pa.Field(nullable=False, isin=["1", "0"])
    acute: Series[str] = pa.Field(nullable=False, isin=["1", "0"])
    transfer_in: Series[str] = pa.Field(nullable=False, isin=["1", "0"])
    ckd_centre: Series[str] = pa.Field(nullable=True)
    historic_tx: Series[bool]


class krt_incident_schema(krt_query_incident_schema):
    centre_code: Series[str]
    satellite_code: Series[str]
    dialtplt: Series[str] = pa.Field(nullable=True, isin=["HD", "HHD", "PD", "TX"])
    timeline_start: Series[pa.DateTime]
    incident: Series[bool]

    class Config:
        coerce = True
        unique = ["ukrdcid", "pid"]


class krt_prevalent_schema(krt_base_schema):
    # centre_code stays nullable: unmapped units are otherised to satellite
    # code 995 but keep a null centre code as krt_prevalent does not filter
    # down to a single centre before returning
    centre_code: Series[str] = pa.Field(nullable=True)
    satellite_code: Series[str]
    dialtplt: Series[str] = pa.Field(nullable=True, isin=["HD", "HHD", "PD", "TX"])


class demog_base_schema(pa.DataFrameModel):
    pid: Series[str]
    ukrdcid: Series[str]
    sendingfacility: Series[str]
    gender: Series[str] = pa.Field(nullable=True)
    ethnic_group: Series[str] = pa.Field(nullable=True)
    birth_time: Series[pa.DateTime] = pa.Field(nullable=True)
    deathtime: Series[pa.DateTime] = pa.Field(nullable=True)

    class Config:
        coerce = True


class ckd_ukrdc_base_schema(pa.DataFrameModel):
    pid: Series[str]
    ukrdcid: Series[str]
    sendingfacility: Series[str]
    sendingextract: Series[str]
    birthtime: Series[pa.DateTime]
    deathtime: Series[pa.DateTime] = pa.Field(nullable=True)
    healthcarefacilitycode: Series[str]
    healthcarefacilitydesc: Series[str] = pa.Field(nullable=True)
    admitreasoncode: Series[str]
    admitreasoncodestd: Series[str]
    admitreasondesc: Series[str] = pa.Field(nullable=True)
    fromtime: Series[pa.DateTime]
    totime: Series[pa.DateTime] = pa.Field(nullable=True)
    sex: Series[str]
    ethnicgroupcode: Series[str] = pa.Field(nullable=True)
    ethnicgroupdesc: Series[str] = pa.Field(nullable=True)
    ukkaethnicity: Series[str] = pa.Field(nullable=True)
    registry_code_type: Series[str]
    lab_egfr_min: Series[str] = pa.Field(nullable=True)
    max_creatinine: Series[str] = pa.Field(nullable=True)
    max_creatinine_units: Series[str] = pa.Field(nullable=True)
    max_creatinine_date: Series[pa.DateTime] = pa.Field(nullable=True)

    class Config:
        coerce = True


class ckd_prevalent_schema(ckd_ukrdc_base_schema):
    pid: Series[str] = pa.Field(unique=True)
    ukrdcid: Series[str] = pa.Field(unique=True)

    decimalage: Series[float]
    age: Series[str]
    adult_paed: Series[str]
    egfr_min: Series[int]

    class Config:
        coerce = True


class ckd_treatment_archive_base_schema(pa.DataFrameModel):
    sendingfacility: Series[str]
    patientid: Series[str]
    organization: Series[str]
    numbertype: Series[str]
    admitreasoncode: Series[str]
    admitreasoncodestd: Series[str]
    admitreasondesc: Series[str] = pa.Field(nullable=True)
    fromtime: Series[pa.DateTime]
    totime: Series[pa.DateTime] = pa.Field(nullable=True)

    class Config:
        coerce = True
