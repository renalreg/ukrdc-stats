-- Quarterly incident KRT counts for staging centres
-- Assumes pg_temp.incident_cohort has already been created in the current session.
-- Run scripts/approximation_functions.sql first.
-- Quarters from Q3 2024 to Q2 2026 (8 quarters)

WITH quarters AS (
    SELECT quarter_start::date AS quarter_start
    FROM generate_series(
        DATE '2024-07-01',
        DATE '2026-04-01',
        interval '3 months'
    ) AS quarter_start
),
centres AS (
    SELECT unnest(ARRAY['RFBAK', 'RM574', 'RJZ', 'RJ121', 'RJ122']) AS sendingfacility
),
grid AS (
    SELECT q.quarter_start, c.sendingfacility
    FROM quarters q
    CROSS JOIN centres c
),
incidents AS (
    SELECT
        q.quarter_start,
        i.sendingfacility,
        i.pid
    FROM quarters q
    CROSS JOIN LATERAL pg_temp.incident_cohort(
        ARRAY['RFBAK', 'RM574', 'RJZ', 'RJ121', 'RJ122'],
        q.quarter_start,
        (q.quarter_start + interval '3 months')::date
    ) i
)
SELECT
    g.quarter_start,
    g.sendingfacility,
    COUNT(DISTINCT i.pid) AS incident_count
FROM grid g
LEFT JOIN incidents i
    ON i.quarter_start = g.quarter_start
   AND i.sendingfacility = g.sendingfacility
GROUP BY g.quarter_start, g.sendingfacility
ORDER BY g.quarter_start, g.sendingfacility;
