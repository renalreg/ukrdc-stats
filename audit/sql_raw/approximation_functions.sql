-- This should be run once and creates a set of functions that return lists of
-- patients that approximate the ukrdc-stats cohorts

BEGIN;

CREATE OR REPLACE FUNCTION pg_temp.incident_cohort(
    centres text [],
    cohort_start date,
    cohort_end date
)
RETURNS TABLE (
    ukrdcid varchar,
    pid varchar,
    start_date date,
    end_date date,
    sendingfacility varchar,
    treatment_centre_start varchar,
    treatment_centre_90days varchar,
    treatment_type_start varchar,
    discharge_90days varchar
) AS $$
BEGIN
    RETURN QUERY
    WITH eligible_patients AS (
        SELECT p.pid, p.ukrdcid, p.sendingfacility, pat.birthtime, pat.deathtime
        FROM patientrecord p
        INNER JOIN patient pat ON pat.pid = p.pid
        WHERE p.sendingextract = 'UKRDC'
          AND p.sendingfacility = ANY(centres)
    ),
    t1 AS (
        SELECT DISTINCT ON (ep.pid)
            ep.pid,
            ep.ukrdcid,
            ep.sendingfacility,
            ep.birthtime,
            ep.deathtime,
            cohort_start AS window_start,
            t.healthcarefacilitycode AS treatment_centre_start,
            t.fromtime AS t1_fromtime,
            t.totime AS t1_totime,
            t.admitreasoncode AS modality_start,
            mc.registry_code_type AS treatment_type_start,
            mc.acute
        FROM eligible_patients ep
        INNER JOIN treatment t ON t.pid = ep.pid
        INNER JOIN modality_codes mc ON t.admitreasoncode = mc.registry_code
        WHERE t.fromtime >= cohort_start
          AND t.fromtime < cohort_end
          AND mc.registry_code_type IN ('TX', 'HD', 'PD')
        ORDER BY ep.pid, t.fromtime ASC, t.totime DESC NULLS FIRST
    ),
    t2 AS (
        SELECT DISTINCT ON (t1.pid)
            t1.pid,
            t.healthcarefacilitycode AS treatment_centre_90days,
            t.totime AS t2_totime,
            t.admitreasoncode AS modality_90days,
            mc.registry_code_type AS treatment_type_90days,
            t.dischargereasoncode AS discharge_90days
        FROM t1
        INNER JOIN treatment t ON t.pid = t1.pid
        INNER JOIN modality_codes mc ON t.admitreasoncode = mc.registry_code
        WHERE t.fromtime >= t1.t1_fromtime
          AND (
              t.totime >= t1.t1_fromtime + interval '90 days'
              OR t.totime IS NULL
          )
          AND mc.registry_code_type IN ('TX', 'HD', 'PD')
        ORDER BY t1.pid, t.fromtime ASC, t.totime DESC NULLS FIRST
    ),
    patients AS (
        SELECT t1.ukrdcid,
               t1.pid,
               t1.sendingfacility,
               t1.birthtime,
               t1.deathtime,
               t1.window_start,
               t1.treatment_centre_start,
               t1.t1_fromtime,
               t1.t1_totime,
               t1.modality_start,
               t1.treatment_type_start,
               t1.acute,
               t2.treatment_centre_90days,
               t2.t2_totime,
               t2.modality_90days,
               t2.treatment_type_90days,
               t2.discharge_90days
        FROM t1
        LEFT JOIN t2 ON t2.pid = t1.pid
        WHERE t2.pid IS NOT NULL
           OR (
               t1.acute::integer = 0
               AND t1.deathtime IS NOT NULL
               AND t1.t1_fromtime + interval '90 days' > t1.deathtime
           )
    )
    SELECT p.ukrdcid,
           p.pid,
           p.window_start AS start_date,
           cohort_end AS end_date,
           p.sendingfacility,
           p.treatment_centre_start,
           p.treatment_centre_90days,
           p.treatment_type_start,
           p.discharge_90days
    FROM patients p
    ORDER BY p.sendingfacility, p.window_start;
END;
$$ LANGUAGE plpgsql;




-- Point-prevalence cohort for a single date.
-- Returns every treatment row active on the supplied prevalence_point.

CREATE OR REPLACE FUNCTION pg_temp.prevalent_cohort(
    centres text [],
    prevalence_point date
)
RETURNS TABLE (
    ukrdcid varchar,
    pid varchar,
    sendingfacility varchar,
    birthtime date,
    deathtime date,
    healthcarefacilitycode varchar,
    admitreasoncode varchar,
    registry_code_type varchar,
    fromtime date,
    totime date
) AS $$
BEGIN
    RETURN QUERY
    WITH eligible_patients AS (
        SELECT p.pid, p.ukrdcid, p.sendingfacility, pat.birthtime, pat.deathtime
        FROM patientrecord p
        INNER JOIN patient pat ON pat.pid = p.pid
        WHERE p.sendingextract = 'UKRDC'
          AND p.sendingfacility = ANY(centres)
    ),
    valid_treatments AS (
        SELECT t.pid,
               t.healthcarefacilitycode,
               t.fromtime,
               t.totime,
               t.admitreasoncode,
               mc.registry_code_type
        FROM treatment t
        INNER JOIN modality_codes mc ON mc.registry_code = t.admitreasoncode
        WHERE mc.registry_code_type IN ('TX', 'HD', 'PD')
    ),
    first_valid_treatment AS (
        SELECT DISTINCT ON (pid)
            pid,
            fromtime AS first_fromtime
        FROM valid_treatments
        ORDER BY pid, fromtime ASC
    ),
    active_treatments AS (
        SELECT DISTINCT ON (ep.pid)
            ep.pid,
            ep.ukrdcid,
            ep.sendingfacility,
            ep.birthtime,
            ep.deathtime,
            vt.healthcarefacilitycode,
            vt.admitreasoncode,
            vt.registry_code_type,
            vt.fromtime,
            vt.totime,
            fvt.first_fromtime
        FROM eligible_patients ep
        INNER JOIN valid_treatments vt ON vt.pid = ep.pid
        INNER JOIN first_valid_treatment fvt ON fvt.pid = ep.pid
        WHERE vt.fromtime <= prevalence_point
          AND (vt.totime >= prevalence_point OR vt.totime IS NULL)
        ORDER BY ep.pid, vt.fromtime ASC, vt.totime DESC NULLS FIRST
    ),
    incident_patients AS (
        SELECT DISTINCT i.pid
        FROM pg_temp.incident_cohort(
            centres,
            (prevalence_point - interval '90 days')::date,
            prevalence_point
        ) i
    )
    SELECT at.ukrdcid,
           at.pid,
           at.sendingfacility,
           at.birthtime,
           at.deathtime,
           at.healthcarefacilitycode,
           at.admitreasoncode,
           at.registry_code_type,
           at.fromtime,
           at.totime
    FROM active_treatments at
    WHERE at.first_fromtime < prevalence_point - interval '90 days'
       OR at.pid IN (SELECT ip.pid FROM incident_patients ip);
END;
$$ LANGUAGE plpgsql;
COMMIT;
