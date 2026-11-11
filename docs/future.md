# Future work

Placeholders and planned changes. Each item is in the code or the changelog.

- **Incident CKD cohort:** `ckd_incident` in `cohorts/base.py` is a stub.
- **ESKD labeller:** `eskd` in `labellers/clinical.py` raises `NotImplementedError`.
- **Under-maintenance KRT fields:** `incentre_dialysis_frequency`, `incentre_time_dialysed`,
  `incident_initial_access` and `prevalent_most_recent_access` return placeholders until agreed definitions exist.
- **Labeller signatures:** move all labellers to the cohort-first contract in [Labellers](concepts/labellers.md).
- **Pydantic response models:** may move into `ukrdc-fastapi`.
- **Cohort grain:** `ckd_prevalent` and `krt_incident` enforce one row per patient in their schemas.
  The intended default is one row per patient per centre. Align the schemas, or confirm the intent.
