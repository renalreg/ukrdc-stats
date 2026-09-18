import datetime as dt
import pandas as pd
from sqlalchemy.orm import Session
from ukrdc_stats.exceptions import MissingColumnError
from ukrdc_stats.labellers.query import query_results
from ukrdc_stats.utils.data import egfr as calculate_egfr


def egfr(
    session: Session,
    patient_cohort: pd.DataFrame,
    prevalence_point: dt.datetime,
    mode="min",
) -> pd.DataFrame:
    """
    Appends the egfr for each patient based on mode. This can be either
    calculated or lab reported.
    """

    required_columns = {"pid", "birthtime", "sex"}
    missing_cols = required_columns - set(patient_cohort.columns)
    if missing_cols:
        raise MissingColumnError(
            f"Patient cohort must contain columns: {', '.join(missing_cols)}"
        )

    # Lab eGFR codes: aggregate to min per pid in SQL to avoid fetching
    # thousands of raw rows. QBLA1 (creatinine) needs raw rows for per-row
    # eGFR calculation.
    lab_egfr_codes = ["QBLAB", "QBLAL", "QBLAP"]
    lab_egfr_data = query_results(
        session=session,
        pids=patient_cohort["pid"].tolist(),
        test_codes=lab_egfr_codes,
        to_time=prevalence_point,
        mode="min",
    )
    creatinine_data = query_results(
        session=session,
        pids=patient_cohort["pid"].tolist(),
        test_codes=["QBLA1"],
        to_time=prevalence_point,
        mode="max",
    )
    egfr_data = pd.concat([lab_egfr_data, creatinine_data], ignore_index=True)

    # Clean data
    egfr_data["resultvalue"] = pd.to_numeric(
        egfr_data["resultvalue"].astype(str).str.replace(r"[<>]", "", regex=True),
        errors="coerce",
    )
    egfr_data = egfr_data.dropna(subset=["resultvalue"])

    # Isolate eGFR and rename the column
    egfr_data.loc[
        egfr_data["serviceidcode"].isin(["QBLAB", "QBLAL", "QBLAP"]), "egfr"
    ] = egfr_data.loc[
        egfr_data["serviceidcode"].isin(["QBLAB", "QBLAL", "QBLAP"]), "resultvalue"
    ]

    # We need birthtime and sex from the patient_cohort to calculate eGFR
    egfr_data = egfr_data.merge(
        patient_cohort[["pid", "birthtime", "sex"]], on="pid", how="inner"
    )

    # Calculate eGFR row by row for null eGFRs (which are the QBLA1 creatinine results)
    null_egfr_mask = egfr_data["egfr"].isnull()

    if null_egfr_mask.any():
        egfr_data.loc[null_egfr_mask, "egfr"] = egfr_data[null_egfr_mask].apply(
            lambda row: calculate_egfr(
                scr=row["resultvalue"],
                scr_unit=row["resultvalueunits"],
                scr_date=row["observationtime"],
                dob=row["birthtime"],
                sex=row["sex"],
            ),
            axis=1,
        )

    # Drop rows where we couldn't get an eGFR (either lab or calculated)
    egfr_data = egfr_data.dropna(subset=["egfr"])

    # Prioritise calculated eGFR over lab eGFR for results at the same time
    priority_map = {"QBLA1": 1, "QBLAB": 3, "QBLAL": 3, "QBLAP": 2}
    egfr_data["priority"] = egfr_data["serviceidcode"].map(priority_map)
    egfr_data = egfr_data.sort_values(["pid", "observationtime", "priority"])
    egfr_data = egfr_data.drop_duplicates(
        subset=["pid", "observationtime"], keep="first"
    )
    egfr_data = egfr_data.drop(columns=["priority"])

    # Select which egfr result to keep
    if mode == "min":
        egfr_data = egfr_data.sort_values("egfr").drop_duplicates(
            subset=["pid"], keep="first"
        )
    elif mode == "last":
        egfr_data = egfr_data.sort_values("observationtime").drop_duplicates(
            subset=["pid"], keep="last"
        )
    elif mode == "all":
        pass
    else:
        raise NotImplementedError(
            f"Mode '{mode}' is not supported. Must be 'min', 'last', or 'all'"
        )

    col_name = f"egfr_{mode}"
    egfr_data = egfr_data.rename(
        columns={"egfr": col_name, "observationtime": "egfr_date"}
    )

    # Merge the final deduplicated eGFRs back onto the patient cohort
    patient_cohort = patient_cohort.merge(
        egfr_data[["pid", col_name, "egfr_date"]], on="pid", how="left"
    )

    return patient_cohort


def systolic():
    pass


def diastolic():
    pass
