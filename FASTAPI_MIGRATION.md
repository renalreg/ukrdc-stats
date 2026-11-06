# Migrating `ukrdc-fastapi` to `ukrdc-stats` v3

This guide covers the changes required in `ukrdc-fastapi` when upgrading `ukrdc-stats` from 2.x to 3.0.0.

## Required changes

These changes will stop the application from working if they are skipped.

### 1. Bump the dependency

In `pyproject.toml`, update the `ukrdc-stats` constraint and ensure the surrounding pins are compatible:

```toml
[tool.poetry.dependencies]
ukrdc-stats = "^3.0.0"
ukrdc-sqla  = "^4.0.0"   # already required by ukrdc-stats v3
pydantic    = "^2.0"      # already required
python      = "^3.12"    # already required
```

### 2. Remove or replace the `/demographics` endpoint

`DemographicStatsCalculator` and `DemographicsStats` have been removed from `ukrdc-stats`. The following imports will fail on upgrade:

```python
# ukrdc_fastapi/routers/api/facilities/stats.py
from ukrdc_stats.calculators.demographics import DemographicsStats
```

```python
# ukrdc_fastapi/query/facilities/stats.py
from ukrdc_stats.calculators.demographics import (
    DemographicsStats,
    DemographicStatsCalculator,
)
```

Choose one of the following approaches:

- **Option A — drop the endpoint:** Remove `/demographics` entirely. The same age/sex/ethnicity breakdowns are now included inside `/krt` (`incident_krt_*` and `prevalent_krt_*`).
- **Option B — re-implement with CKD:** Replace it with `PrevalentCKDCalculator` and return `UnitLevelCKDStats` if the endpoint should describe the prevalent CKD population.
- **Option C — re-implement with KRT:** Replace `DemographicStatsCalculator(...)` with `KRTStatsCalculator(session, facility, from_time, to_time).extract_stats().all` and return the relevant `*_krt_age/sex/ethnicity` fields.

### 3. Update the `/krt` endpoint

The response model class name is still `UnitLevelKRTStats`, but the nested fields have changed:

| Old field (v2) | New field (v3) |
|---|---|
| `incident_krt` | `incident_krt_modality` |
| `prevalent_krt` | `prevalent_krt_modality` |

New fields are also present:

- Incident: `incident_krt_age`, `incident_krt_sex`, `incident_krt_ethnicity`, `incident_krt_careplanning`
- Prevalent: `prevalent_krt_age`, `prevalent_krt_sex`, `prevalent_krt_ethnicity`, `prevalent_krt_careplanning`

The four old dialysis/access fields are now placeholders:

- `incentre_dialysis_frequency`
- `incentre_time_dialysed`
- `incident_initial_access`
- `prevalent_most_recent_access`

They return empty `x`/`y` arrays with an "Under maintenance" description.

The calculator call itself keeps the same shape:

```python
return KRTStatsCalculator(
    ukrdc3,
    facility.facilitycode,
    from_time=from_time,
    to_time=to_time,
).extract_stats()
```

### 4. Update tests and frontend

- Update any unit tests asserting the old field names (`incident_krt`, `prevalent_krt`, `gender`, `ethnic_group`, etc.).
- Update the dashboard frontend to consume the new modality, demographic, and careplanning field names.

---

## Caching changes

### Invalidate old cached values

The JSON shape of `UnitLevelKRTStats` has changed (renamed fields, new fields, and placeholder fields). Any cached v2 responses under the existing Redis keys will not deserialize cleanly into the v3 Pydantic model.

Before deploying:

- Either change the cache prefix to version the keys (e.g. `facilities:stats:krt:v3:...`).
- Or purge the relevant Redis keys (`facilities:stats:krt:*` and `facilities:stats:demographics:*`).
- Alternatively, set a short TTL on the existing keys so they expire before the new code goes live.

If `/demographics` is removed, delete all keys under `facilities:stats:demographics:*` and remove `FacilityCachePrefix.DEMOGRAPHICS` from `ukrdc_fastapi/utils/cache.py` if it is no longer used.

### KRT cache keys must encode the time window

`KRTStatsCalculator` now produces different results for different `from_time`/`to_time` windows. The cache key must include the start and end dates so that requests for different windows do not collide.

The background pre-calculation task should cache several fixed windows. For example:

```python
today_date = datetime.now().date()
quarter_start_month = (today_date.month - 1) // 3 * 3 + 1
quarter_start_date = date(year=today_date.year, month=quarter_start_month, day=1)

for facility_code in facilities_to_cache:
    for end_date in [today_date, quarter_start_date]:
        for period in [30, 90, 365]:
            start_date = end_date - timedelta(days=period)
            cache = BasicCache(
                get_redis(),
                DynamicCacheKey(
                    FacilityCachePrefix.KRT,
                    facility_code,
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d"),
                ),
            )
            if not cache.exists:
                cache.set(
                    get_facility_dialysis_stats(
                        ukrdc3,
                        facility_code,
                        since=datetime.combine(start_date, time.min),
                        until=datetime.combine(end_date, time.max),
                    ),
                    expire=settings.cache_facilities_stats_dialysis_seconds,
                )
```

For the live `/krt` endpoint:

- When `since` and/or `until` are provided, include them in the `DynamicCacheKey`.
- For the no-date case, decide on a stable key strategy. Because the calculator defaults to a rolling window, a single unversioned key will become stale unless it expires quickly or is mapped to one of the pre-calculated windows.

### Add a CKD cache prefix if you add `/ckd`

If you implement the optional `/ckd` endpoint (see below), add a `CKD` value to `FacilityCachePrefix` in `ukrdc_fastapi/utils/cache.py`:

```python
class FacilityCachePrefix(CachePrefix):
    ROOT = "facilities:root"
    EXTRACTS = "facilities:extracts"
    FEEDSHARE = "facilities:feedshare"
    DEMOGRAPHICS = "facilities:stats:demographics"  # remove if no longer used
    KRT = "facilities:stats:krt"
    CKD = "facilities:stats:ckd"
    SATELLITES = "facilities:satellites"
```

Key by facility code and prevalence point, for example:

```python
cache_key = DynamicCacheKey(
    FacilityCachePrefix.CKD, code, prevalence_point.isoformat()
)
```

You will also need to add a TTL setting (e.g. `cache_facilities_stats_ckd_seconds`) if the endpoint is cached.

---

## New functionality (optional)

These changes are not required for the upgrade, but they make use of the new v3 features.

### 1. Add a `/ckd` endpoint

`PrevalentCKDCalculator` is now exported. A minimal new endpoint would look like:

```python
from ukrdc_stats.calculators.ckd import PrevalentCKDCalculator, UnitLevelCKDStats

@router.get("/ckd", response_model=UnitLevelCKDStats)
def facility_stats_ckd(
    code: str,
    request: Request,
    response: Response,
    redis: Redis = Depends(get_redis),
    ukrdc3: Session = Depends(get_ukrdc3),
    user: UKRDCUser = Security(get_current_user),
    until: str | None = None,
):
    assert_facility_permission(code, user)

    prevalence_point = (
        datetime.strptime(until + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        if until
        else datetime.now()
    )

    cache_key = DynamicCacheKey(FacilityCachePrefix.CKD, code, until or "now")
    cache = cache_factory(cache_key)(request=request, response=response, redis=redis)

    if not cache.exists:
        cache.set(
            PrevalentCKDCalculator(
                ukrdc3, code, prevalence_point=prevalence_point
            ).extract_stats(),
            expire=settings.cache_facilities_stats_ckd_seconds,
        )

    cache.prepare_response()
    return UnitLevelCKDStats(**cache.get())
```

### 2. Use the new demographic and careplanning fields

`/krt` now returns age, sex, ethnicity, and careplanning breakdowns for both incident and prevalent cohorts. These can be displayed without making separate `/demographics` calls.

### 3. Satellite drilldown and cohort reports

Both `UnitLevelKRTStats` and `UnitLevelCKDStats` contain:

- `all`: whole-centre aggregated stats.
- `units`: per-satellite-unit stats.

In addition, `KRTStatsCalculator.generate_cohort_report()` and `PrevalentCKDCalculator.generate_cohort_report()` return a long-format `BaseTable` that can be exported to CSV or converted to a Pandas DataFrame.
