"""
Experimental script which aims to test the concept of using local ai to do 
qualatative testing on row level data with a local llama.cpp

This should definitely NOT be pointed at a cloud provider under any
circumstances. 
"""

import json
import urllib.request
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import dotenv_values
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session
from ukrdc_sqla.ukrdc import (
    DialysisSession,
    ModalityCodes,
    Name,
    Patient,
    PatientRecord,
    Transplant,
    Treatment,
)

from ukrdc_stats.utils.database import get_sessionmaker

MISMATCH_INPUT_PATH = Path(".do_not_commit/prevalence_mismatches.csv")
REASONS_OUTPUT_PATH = Path(".do_not_commit/prevalence_mismatch_reasons.csv")
LLAMA_API = "http://127.0.0.1:9931/v1/chat/completions"
SERVER = "ukrdc_live"
PREVALENCE_POINT = "2024-12-31"
DEBUG = True
DEBUG_ROWS_PER_CATEGORY = 5

config = dotenv_values(".env")
KEYPATH = config.get("UKRDC_STATS_KEYPATH")


def query_patient(ukrdcid: str, session: Session) -> pd.DataFrame:
    """Return one row per treatment for a patient, with birth and death dates."""
    first_name = (
        select(Name.pid, Name.family, Name.given)
        .ext(distinct_on(Name.pid))
        .order_by(Name.pid, Name.idx)
        .subquery()
    )
    query = (
        select(
            PatientRecord.pid,
            first_name.c.family,
            first_name.c.given,
            PatientRecord.sendingfacility,
            PatientRecord.sendingextract,
            Patient.birthtime,
            Patient.deathtime,
            Treatment.healthcarefacilitycode,
            Treatment.admitreasoncode,
            Treatment.admissionsourcecode,
            Treatment.dischargereasoncode,
            Treatment.dischargelocationcode,
            ModalityCodes.registry_code_type,
            ModalityCodes.end_of_care,
            ModalityCodes.acute,
            ModalityCodes.transfer_in,
            Treatment.fromtime,
            Treatment.totime,
        )
        .select_from(PatientRecord)
        .join(Patient, Patient.pid == PatientRecord.pid)
        .join(Treatment, Treatment.pid == PatientRecord.pid)
        .outerjoin(
            ModalityCodes, ModalityCodes.registry_code == Treatment.admitreasoncode
        )
        .outerjoin(first_name, first_name.c.pid == PatientRecord.pid)
        .where(PatientRecord.ukrdcid == ukrdcid)
    )
    return pd.DataFrame(session.execute(query).mappings().all())


def query_dialysis_bounds(ukrdcid: str, session: Session) -> pd.DataFrame:
    """Return the time of a patient's first and last dialysis session."""
    query = (
        select(
            func.min(DialysisSession.procedure_time).label("first_session"),
            func.max(DialysisSession.procedure_time).label("last_session"),
        )
        .select_from(PatientRecord)
        .join(DialysisSession, DialysisSession.pid == PatientRecord.pid)
        .where(PatientRecord.ukrdcid == ukrdcid)
    )
    return pd.DataFrame(session.execute(query).mappings().all())


def query_transplants(ukrdcid: str, session: Session) -> pd.DataFrame:
    """Return a patient's transplant procedures and any recorded graft failure date."""
    query = (
        select(
            Transplant.proceduretime,
            Transplant.proceduretypecode,
            Transplant.tra64.label("graft_failure_date"),
        )
        .select_from(PatientRecord)
        .join(Transplant, Transplant.pid == PatientRecord.pid)
        .where(PatientRecord.ukrdcid == ukrdcid)
    )
    return pd.DataFrame(session.execute(query).mappings().all())


PROMPT = """
You are a renal data quality analyst for the UKKA prevalent KRT dataset.
Prevalence point: {prevalence_point}.

A UKRDC-derived cohort has flagged this patient:
- UKRDC view: {ukrdc_modality} at {ukrdc_centre}
- Published UKKA dataset: {ukrr_modality} at {ukrr_centre}
- Flag: {match_category}

Treatment records from the UKRDC (CSV, fromtime and totime are the start and end of each treatment):
{timeline}

Dialysis session bounds (CSV):
{dialysis_bounds}

Transplant treatments (CSV):
{transplants}

{focus}

Using only the treatment records, give the clinical reasons the patient may be missing or differently recorded in the published dataset. Rank the reasons from most to least likely. Do not invent facts that are not in the records.

Respond with JSON only, with keys:
"explanation": a short paragraph (maximum 3 sentences) giving the most likely clinical reason,
"confidence": a number from 0 to 1,
"criticality": a number from 0 to 1 for how much the mismatch affects the published prevalence count.
"""

DATA_QUALITY_CHECKS = (
    " Also check data quality: acute treatments not closed with an end date; open treatments continuing after "
    "death or a transfer; overlapping or duplicate treatment records from different sending facilities or "
    "extracts; admit reason codes with no ModalityCodes match (blank registry_code_type); missing health care "
    "facility, admission source, or discharge reason and location; treatments with no end date that are older "
    "than the most recent dialysis session; transplants with no procedure time; and dialysis sessions that stop "
    "well before the prevalence point, suggesting a stale feed."
)

CATEGORY_FOCUS = {
    "not_ukrr": (
        "The patient has a UKRDC kidney treatment but no matching record in the published dataset. "
        "Consider: treatment ended or stopped before the prevalence point; death before the prevalence point; "
        "treatment started after the prevalence point; a short gap that split the episode; conservative care or "
        "CKD only rather than KRT; a transplant that failed or is not reported as active; "
        "a missing or unmatched NHS number preventing linkage."
        + DATA_QUALITY_CHECKS
    ),
    "ukrr_centre_mismatch": (
        "The patient is in the published dataset but at a different centre. "
        "Consider: a transfer between centres close to the prevalence point; a satellite reporting to a main "
        "centre or the reverse; a transplant recorded at the supervising centre; the admission source or sending "
        "facility differing from the treatment centre; a recent move with the older record still open."
        + DATA_QUALITY_CHECKS
    ),
    "ukrr_modality_mismatch": (
        "The patient is in the published dataset at the same centre but with a different modality. "
        "Consider: a switch between HD, PD or transplant close to the prevalence point; overlapping treatment "
        "records where the open one differs; HHD recorded as HD; a transplant or treatment end date near the "
        "prevalence point; a discharge reason or location suggesting a change of modality not yet reflected."
        + DATA_QUALITY_CHECKS
    ),
}


if not KEYPATH:
    raise RuntimeError(
        "Missing UKRDC_STATS_KEYPATH. Set it in your environment or in a .env file."
    )

mismatches = pd.read_csv(MISMATCH_INPUT_PATH, dtype=str).fillna("no record")
if DEBUG:
    mismatches = mismatches.groupby("match_category").head(DEBUG_ROWS_PER_CATEGORY)
cache: dict[str, dict[str, Any]] = {}
reasons: list[dict[str, Any]] = []

with get_sessionmaker(SERVER, keypath=KEYPATH, caching=False)() as session:
    for _, row in mismatches.iterrows():
        history = query_patient(row["ukrdcid"], session)
        timeline = history[
            [
                "sendingfacility",
                "healthcarefacilitycode",
                "admitreasoncode",
                "admissionsourcecode",
                "dischargereasoncode",
                "dischargelocationcode",
                "registry_code_type",
                "end_of_care",
                "acute",
                "transfer_in",
                "fromtime",
                "totime",
            ]
        ].to_csv(index=False)
        dialysis_bounds = query_dialysis_bounds(row["ukrdcid"], session).to_csv(
            index=False
        )
        transplants = query_transplants(row["ukrdcid"], session).to_csv(index=False)
        prompt = PROMPT.format(
            ukrdc_modality=row["dialtplt"],
            ukrdc_centre=row["centre_code"],
            ukrr_modality=row["trt"],
            ukrr_centre=row["centre"],
            match_category=row["match_category"],
            timeline=timeline,
            dialysis_bounds=dialysis_bounds,
            transplants=transplants,
            prevalence_point=PREVALENCE_POINT,
            focus=CATEGORY_FOCUS[row["match_category"]],
        )
        # Identical prompts (same patient timeline and mismatch) reuse the model reply
        if prompt not in cache:
            payload = json.dumps(
                {
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 1,
                    "chat_template_kwargs": {"enable_thinking": True},
                }
            ).encode()
            request = urllib.request.Request(
                LLAMA_API, data=payload, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                reply = json.load(response)["choices"][0]["message"]["content"]
            # Keep the raw text if the model does not return valid JSON
            try:
                cache[prompt] = json.loads(reply)
            except json.JSONDecodeError:
                cache[prompt] = {
                    "explanation": reply,
                    "confidence": None,
                    "criticality": None,
                }
        reasons.append(cache[prompt])

pd.concat([mismatches, pd.DataFrame(reasons)], axis=1).to_csv(
    REASONS_OUTPUT_PATH, index=False
)
print(f"Explained {len(mismatches)} mismatches with {len(cache)} model queries")