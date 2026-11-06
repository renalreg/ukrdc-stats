import datetime as dt

import pandas as pd

from ukrdc_stats.calculators.builders import build_labelled2d, under_maintenance
from ukrdc_stats.calculators.ckd import PrevalentCKDCalculator
from ukrdc_stats.calculators.krt import KRTStatsCalculator
from ukrdc_stats.calculators.models import KRTStats


def _krt_cohort() -> pd.DataFrame:
    """Wide cohort as produced by extract_patient_cohort: one row per patient
    per incident/prevalent cohort, labelled with the demographic trio."""
    return pd.DataFrame(
        {
            "ukrdcid": ["1066", "1067", "1069", "1087", "1100", "1066"],
            "satellite_code": [
                "HASTINGS",
                "HASTINGS",
                "STAMFORD",
                "STAMFORD",
                "HASTINGS",
                "HASTINGS",
            ],
            "incidprev": [
                "incident",
                "incident",
                "prevalent",
                "prevalent",
                "prevalent",
                "prevalent",
            ],
            "age": ["18-34", "35-54", "35-54", "75+", "55-74", "18-34"],
            "ethnicity": ["White", "White", "Asian", "White", "Missing", "White"],
            "sex": ["Male", "Female", "Male", "Male", "Female", "Male"],
            "dialtplt": ["HD", "PD", "TX", "HD", "HHD", "HD"],
            "assessmentoutcome": [None, None, None, None, None, None],
        }
    )


def _aggregated(cohort: pd.DataFrame, column_attributes: list[str]) -> pd.DataFrame:
    from ukrdc_stats.utils.data import aggregate_data

    return aggregate_data(
        cohort_wide=cohort,
        column_attributes=column_attributes,
        row_attributes=["age", "ethnicity", "sex", "dialtplt"],
    )


def test_build_labelled2d_filters_and_counts():
    aggregated = _aggregated(_krt_cohort(), ["satellite_code", "incidprev"])

    incident_sex = build_labelled2d(
        aggregated,
        "sex",
        "Incident KRT Sex",
        "summary",
        "description",
        {"incidprev": "incident"},
    )
    counts = dict(zip(incident_sex.data.x, incident_sex.data.y))
    assert counts == {"Male": 1, "Female": 1}
    assert incident_sex.metadata.population_size == 2

    # pinning the satellite drills into a single unit
    unit_sex = build_labelled2d(
        aggregated,
        "sex",
        "Prevalent KRT Sex",
        "summary",
        "description",
        {"incidprev": "prevalent", "satellite_code": "STAMFORD"},
    )
    assert dict(zip(unit_sex.data.x, unit_sex.data.y)) == {"Male": 2}


def test_build_labelled2d_all_sums_over_satellites():
    aggregated = _aggregated(_krt_cohort(), ["satellite_code", "incidprev"])

    prevalent_ethnicity = build_labelled2d(
        aggregated,
        "ethnicity",
        "Prevalent KRT Ethnicity",
        "summary",
        "description",
        {"incidprev": "prevalent"},
    )
    counts = dict(zip(prevalent_ethnicity.data.x, prevalent_ethnicity.data.y))
    assert counts == {"White": 2, "Asian": 1, "Missing": 1}


def test_under_maintenance_placeholder():
    placeholder = under_maintenance("In-Centre Dialysis Frequency")
    assert placeholder.data.x == []
    assert placeholder.data.y == []
    assert placeholder.metadata.title == "In-Centre Dialysis Frequency"
    assert "maintenance" in placeholder.metadata.summary.lower()


def test_krt_calculator_extract_stats():
    calculator = KRTStatsCalculator(
        session=None,
        facility="RBATTLE",
        from_time=dt.datetime(2024, 1, 1),
        to_time=dt.datetime(2024, 12, 31),
    )
    # bypass the database by injecting a pre-built cohort
    calculator._patient_cohort = _krt_cohort()

    stats = calculator.extract_stats()

    # whole centre stats sum over both satellites
    incident_ages = dict(
        zip(stats.all.incident_krt_age.data.x, stats.all.incident_krt_age.data.y)
    )
    assert incident_ages["18-34"] == 1
    assert incident_ages["35-54"] == 1
    assert stats.all.metadata.population == 5  # 1066 in both cohorts, counted once

    # modality breakdowns per cohort
    incident_modalities = dict(
        zip(
            stats.all.incident_krt_modality.data.x,
            stats.all.incident_krt_modality.data.y,
        )
    )
    assert incident_modalities == {"HD": 1, "PD": 1}
    prevalent_modalities = dict(
        zip(
            stats.all.prevalent_krt_modality.data.x,
            stats.all.prevalent_krt_modality.data.y,
        )
    )
    assert prevalent_modalities == {"HD": 2, "HHD": 1, "TX": 1}

    # per unit drilldown
    assert set(stats.units) == {"HASTINGS", "STAMFORD"}
    stamford_sex = dict(
        zip(
            stats.units["STAMFORD"].prevalent_krt_sex.data.x,
            stats.units["STAMFORD"].prevalent_krt_sex.data.y,
        )
    )
    assert stamford_sex == {"Male": 2}
    assert stats.units["STAMFORD"].metadata.population == 2

    # the legacy fields survive only as under maintenance placeholders
    for field in (
        "incentre_dialysis_frequency",
        "incentre_time_dialysed",
        "incident_initial_access",
        "prevalent_most_recent_access",
    ):
        placeholder = getattr(stats.all, field)
        assert placeholder.data.x == []
        assert "maintenance" in placeholder.metadata.summary.lower()


def test_krt_generate_cohort_report_matches_extract_shape():
    calculator = KRTStatsCalculator(
        session=None,
        facility="RBATTLE",
        from_time=dt.datetime(2024, 1, 1),
        to_time=dt.datetime(2024, 12, 31),
    )
    calculator._patient_cohort = _krt_cohort()

    report = calculator.generate_cohort_report()
    assert report.headers == [
        "attribute",
        "variable",
        "satellite_code",
        "incidprev",
        "count",
    ]
    report_df = report.to_pandas()
    # each demographic attribute appears in the long format table
    assert set(report_df["attribute"].unique()) == {
        "age",
        "ethnicity",
        "sex",
        "dialtplt",
        "assessmentoutcome",
    }


def test_krt_stats_model_has_no_removed_stats_fields():
    fields = set(KRTStats.model_fields)
    assert {"incident_krt", "prevalent_krt"}.isdisjoint(fields)
    assert {
        "incident_krt_modality",
        "incident_krt_age",
        "incident_krt_ethnicity",
        "incident_krt_sex",
        "prevalent_krt_modality",
        "prevalent_krt_age",
        "prevalent_krt_ethnicity",
        "prevalent_krt_sex",
    } <= fields


def test_ckd_calculator_extract_stats():
    calculator = PrevalentCKDCalculator(
        session=None,
        facility="RBATTLE",
        prevalence_point=dt.datetime(2024, 12, 31),
    )
    calculator._patient_cohort = pd.DataFrame(
        {
            "ukrdcid": ["1215", "1415", "1485"],
            "satellite_code": ["RUNNYMEDE", "AGINCOURT", "BOSWORTH"],
            "age": ["55-74", "35-54", "75+"],
            "ethnicity": ["White", "White", "Black"],
            "sex": ["Male", "Female", "Male"],
            "assessmentoutcome": [None, None, None],
        }
    )

    stats = calculator.extract_stats()

    ages = dict(
        zip(stats.all.prevalent_ckd_age.data.x, stats.all.prevalent_ckd_age.data.y)
    )
    assert ages == {"35-54": 1, "55-74": 1, "75+": 1}
    assert stats.all.metadata.population == 3
    assert set(stats.units) == {"RUNNYMEDE", "AGINCOURT", "BOSWORTH"}
    assert stats.units["BOSWORTH"].metadata.population == 1
