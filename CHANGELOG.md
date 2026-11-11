# Changelog

## [3.0.0] - Unreleased

Major refactor of the stats calculators. The public surface is smaller, the internal pipeline is now split into reusable cohort and labeller modules, and the KRT/CKD calculators now return the same demographic/careplanning statistics.

### Highlights

- New cohort/labeller architecture under `ukrdc_stats.cohorts` and `ukrdc_stats.labellers`.
- `KRTStatsCalculator` now returns incident **and** prevalent modality, age, sex, ethnicity **and** careplanning summaries.
- New `PrevalentCKDCalculator` provides the same demographic/careplanning breakdowns for the prevalent CKD cohort.
- Stats are returned for the whole centre plus a per-satellite `units` map.

### Breaking changes

- **`DemographicStatsCalculator` and `DemographicsStats` have been removed.**
  Demographics are now produced by the KRT and CKD calculators. See `ukrdc_stats/__init__.py`:

  ```@c:\Users\philip.main\Source\dashboard-stats\ukrdc_stats\__init__.py:1-13
  """
  Functions to calculate patient cohort statistics.
  Pydantic classes to return nice plotly friendly output.

  The DemographicStatsCalculator available prior to version 3.0.0 has been
  removed; demographics are now served by the KRT and CKD calculators.
  """

  from ukrdc_stats.calculators.ckd import PrevalentCKDCalculator
  from ukrdc_stats.calculators.krt import KRTStatsCalculator

  __all__ = ["KRTStatsCalculator", "PrevalentCKDCalculator"]
  ```

- **`KRTStats` field changes.** The old `incident_krt` and `prevalent_krt` modality breakdowns are renamed and the model now carries demographic and careplanning fields. The current field list is in `ukrdc_stats/calculators/models.py`:

  ```@c:\Users\philip.main\Source\dashboard-stats\ukrdc_stats\calculators\models.py:148-199
  class KRTStats(JSONModel):
      """
      Container class for the KRT demographic stats. Fields previously derived
      from dialysis sessions are retained for API compatibility but only carry
      an "Under maintenance" placeholder.
      """

      incident_krt_modality: Labelled2d = Field(
          ..., description="Modality breakdown of incident KRT patients"
      )
      prevalent_krt_modality: Labelled2d = Field(
          ..., description="Modality breakdown of prevalent KRT patients"
      )
      incident_krt_age: Labelled2d = Field(
          ..., description="Age breakdown of incident KRT patients"
      )
      incident_krt_ethnicity: Labelled2d = Field(
          ..., description="Ethnicity breakdown of incident KRT patients"
      )
      incident_krt_sex: Labelled2d = Field(
          ..., description="Sex breakdown of incident KRT patients"
      )
      prevalent_krt_age: Labelled2d = Field(
          ..., description="Age breakdown of prevalent KRT patients"
      )
      prevalent_krt_ethnicity: Labelled2d = Field(
          ..., description="Ethnicity breakdown of prevalent KRT patients"
      )
      prevalent_krt_sex: Labelled2d = Field(
          ..., description="Sex breakdown of prevalent KRT patients"
      )
      incident_krt_careplanning: Labelled2d = Field(
          ..., description="Careplanning assessment outcome breakdown of incident KRT patients"
      )
      prevalent_krt_careplanning: Labelled2d = Field(
          ..., description="Careplanning assessment outcome breakdown of prevalent KRT patients"
      )
      incentre_dialysis_frequency: Labelled2d = Field(
          ..., description="Under maintenance: previously per week dialysis frequency"
      )
      incentre_time_dialysed: Labelled2d = Field(
          ..., description="Under maintenance: previously per week time dialysed"
      )
      incident_initial_access: Labelled2d = Field(
          ...,
          description="Under maintenance: previously vascular access on first session",
      )
      prevalent_most_recent_access: Labelled2d = Field(
          ..., description="Under maintenance: previously most recent vascular access"
      )
      metadata: KRTMetadata
  ```

- **Output models moved.** The old `ukrdc_stats.models` package has been removed; Pydantic response models now live in `ukrdc_stats.calculators.models`. In the future this will all potentially be moved into fastapi.
- **Dependency upgrades:** Python 3.12, Pydantic 2.x, SQLAlchemy 2.x, pandas 2.2.x and `ukrdc-sqla` ^4.0.0 are now required.
- **Cached responses must be purged or versioned.** The `UnitLevelKRTStats` JSON shape has changed (renamed fields, new fields, placeholder fields). Any cached v2 responses will fail to deserialize into the v3 model.

### New features

- **`PrevalentCKDCalculator`** (`ukrdc_stats/calculators/ckd.py:34-179`). Calculates age, sex, ethnicity and careplanning stats for the prevalent CKD cohort at a single `prevalence_point`, returning `UnitLevelCKDStats` with `all` and `units` keys.
- **Careplanning summaries** linked to KRT and CKD cohorts. The most recent TPLT (`Transition to Long-Term`) assessment outcome is reported as a separate `*_careplanning` field.
- **Satellite-level drilldown** preserved: `UnitLevelKRTStats.all` and `UnitLevelKRTStats.units` (and the CKD equivalent) keep the existing centre/satellite split.
- **Cohort reports** can be exported as a long-format `BaseTable` via `KRTStatsCalculator.generate_cohort_report()` and `PrevalentCKDCalculator.generate_cohort_report()`.
- **New helper modules:**
  - `ukrdc_stats.cohorts` — cohort construction (`ckd_prevalent`, `krt_incident`, `krt_prevalent`).
  - `ukrdc_stats.labellers` — reusable demographic, clinical, geography and data-quality labellers.
  - `ukrdc_stats.utils.data`, `.database`, `.interval`, `.query` — split out from the old monolithic `utils`.

### Retired / under maintenance

The following fields are kept in the `KRTStats` model for API compatibility but now return empty placeholder data with an "Under maintenance" message:

- `incentre_dialysis_frequency`
- `incentre_time_dialysed`
- `incident_initial_access`
- `prevalent_most_recent_access`

They will be reinstated once reliable definitions are agreed.

### Internal changes

- Replaced the old model package with a single `ukrdc_stats.calculators.models` module that uses Pydantic V2 and camelCase JSON aliasing.
- Migrated from the legacy demographics calculator to shared cohort/labeller functions so the same logic drives the API calculators and the standalone extract scripts.
- Added Pandera schemas for cohort validation (`ukrdc_stats/cohorts/schema.py`) and dogpile caching support.
- KRT stats are now computed for explicit time windows; downstream caches must include the window (e.g. `since`/`until`) in the cache key to avoid collisions.
- Reduced the CKD query chunk size to avoid large PostgreSQL `IN` clause timeouts.
- Fixed the CKD archive session helper so it returns an open session rather than one closed by a context manager.

### Documentation

- MkDocs site (`mkdocs.yml`, `docs/`) with concept guides for cohorts, labellers and utilities, plus an API reference generated from docstrings.
- `calculators` is marked legacy throughout the docs.

### Demo / scripts

- `demo/v3.0.0_changes_for_app_interface.ipynb` documents the new calculator API and example Plotly charts.
- `demo/krt_dashboard.py` is a Marimo pivot-style dashboard over the KRT extract CSVs.
- New scripts: `extract_krt_demog.py`, `extract_krt_demog_ukrr.py`, `extract_ckd_prevalent.py`, `extract_incident_krt_careplanning.py`, `extract_incident_report.py`, `incident_single_centre.py` and `comparison_rfbak_2026_q1.py`.

---

## Migration guide

For `ukrdc-fastapi` migration steps (required changes, caching, and optional new features), see `FASTAPI_MIGRATION.md`.
