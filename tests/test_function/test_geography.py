from types import SimpleNamespace

import pandas as pd
import pytest

from ukrdc_stats.exceptions import MissingColumnError
from ukrdc_stats.labellers.geography import main_satellite_centres


def _relationships(
    extra: list[tuple[str, str, str]] | None = None,
) -> pd.DataFrame:
    """FacilityRelationship rows as pd.read_sql would return them."""
    rows: list[tuple[str, str, str]] = [
        ("MAIN-SATELLITE", "RXX", "RXX01"),
        ("FEED-SHARE", "RYY", "RYY01"),
        ("MAIN-SATELLITE", "RYY01", "RYY01A"),
    ]
    if extra:
        rows.extend(extra)
    return pd.DataFrame(
        rows, columns=["relationshiptype", "parentfacilitycode", "childfacilitycode"]
    )


def _krt_incident_cohort() -> pd.DataFrame:
    """Cohort shaped like the singular_cohort krt_incident passes in, with
    one row per mapping scenario."""
    return pd.DataFrame(
        {
            "ukrdcid": ["A", "B", "C", "D", "E", "F", "G"],
            "sendingfacility": [
                "RXX",  # main centre
                "RXX",  # main centre
                "RYY",  # feedshare main
                "RYY",  # feedshare main
                "RYY",  # feedshare main
                "RXX",
                "RXX",
            ],
            "healthcarefacilitycode": [
                "RXX",  # treated at the main unit
                "RXX01",  # satellite of the main unit
                "RYY01",  # feedshare child
                "RYY01A",  # satellite of the feedshare child
                "RZZ",  # ambiguous feedshare site -> dropped
                "990",  # other code
                "RWW",  # unrelated site -> otherised
            ],
            "dialtplt": ["HD", "HD", "PD", "TX", "HD", "HD", "HD"],
        }
    )


def _codes(result: pd.DataFrame, ukrdcid: str) -> list[str]:
    return result.loc[ukrdcid, ["centre_code", "satellite_code"]].tolist()


def _run(
    monkeypatch: pytest.MonkeyPatch,
    cohort: pd.DataFrame,
    relationships: pd.DataFrame | None = None,
    **kwargs: object,
) -> pd.DataFrame:
    """Run the labeller with a patched FacilityRelationship lookup."""
    if relationships is None:
        relationships = _relationships()
    monkeypatch.setattr(pd, "read_sql", lambda *_args, **_kwargs: relationships.copy())
    result = main_satellite_centres(SimpleNamespace(bind=None), cohort, **kwargs)
    return result.set_index("ukrdcid")


def test_main_sat_centre(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, _krt_incident_cohort(), outlier_mapping_mode="otherise")

    expected = {
        "A": ("RXX", "RXX"),  # main unit maps to itself
        "B": ("RXX", "RXX01"),  # satellite maps to its parent
        "C": ("RYY01", "RYY01"),  # feedshare child maps to itself
        "D": ("RYY01", "RYY01A"),  # feedshare satellite maps to feedshare child
        "F": ("RXX", "990"),  # other codes pass through to sendingfacility
    }
    for ukrdcid, (centre, satellite) in expected.items():
        assert _codes(result, ukrdcid) == [centre, satellite]

    # otherised: satellite rewritten to 995, centre left NaN
    assert pd.isna(result.loc["G", "centre_code"])
    assert result.loc["G", "satellite_code"] == "995"

    # ambiguous feedshare row dropped
    assert "E" not in result.index


def test_row_scoped_cohort_all_rows_attributed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row-scoped queries (ckd_prevalent, krt_prevalent) only return rows
    treated at the centre or its satellites, so every row must come out
    attributable in the default passthrough mode."""
    cohort = pd.DataFrame(
        {
            "ukrdcid": ["A", "B", "F"],
            "sendingfacility": ["RXX", "RXX", "RXX"],
            "healthcarefacilitycode": ["RXX", "RXX01", "990"],
        }
    )
    result = _run(monkeypatch, cohort)

    assert _codes(result, "A") == ["RXX", "RXX"]
    assert _codes(result, "B") == ["RXX", "RXX01"]
    assert _codes(result, "F") == ["RXX", "990"]
    assert result["centre_code"].notna().all()


def test_other_centre_rows_classified_for_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """krt_incident returns all treatments for its patients, including
    other centres. Self-consistent other-centre rows keep their own centre
    code; rows treated elsewhere but sent under the queried centre's feed
    stay unattributed, so the final centre_code == centre filter excludes
    both."""
    cohort = pd.DataFrame(
        {
            "ukrdcid": ["H", "I"],
            "sendingfacility": ["RBB", "RXX"],
            "healthcarefacilitycode": ["RBB", "RWW"],
        }
    )
    result = _run(monkeypatch, cohort)

    assert _codes(result, "H") == ["RBB", "RBB"]
    assert pd.isna(result.loc["I", "centre_code"])
    assert result.loc["I", "satellite_code"] == "RWW"


def test_drop_outliers_removes_unattributed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort = pd.DataFrame(
        {
            "ukrdcid": ["A", "I"],
            "sendingfacility": ["RXX", "RXX"],
            "healthcarefacilitycode": ["RXX", "RWW"],
        }
    )
    result = _run(monkeypatch, cohort, drop_outliers=True)

    assert list(result.index) == ["A"]


def test_missing_column_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    cohort = pd.DataFrame({"ukrdcid": ["A"], "sendingfacility": ["RXX"]})
    with pytest.raises(MissingColumnError):
        _run(monkeypatch, cohort)


@pytest.mark.xfail(
    reason=(
        "drop-order bug: feedshare main-unit rows are deleted at the "
        "unassigned drop before the hcf == sf mapping can rescue them"
    ),
    strict=True,
)
def test_feedshare_main_unit_row_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    """A patient treated at the feedshare main unit itself is determinable
    (centre = sendingfacility) and must not be silently deleted."""
    cohort = pd.DataFrame(
        {
            "ukrdcid": ["J"],
            "sendingfacility": ["RYY"],
            "healthcarefacilitycode": ["RYY"],
        }
    )
    result = _run(monkeypatch, cohort)

    assert _codes(result, "J") == ["RYY", "RYY"]


@pytest.mark.xfail(
    reason=(
        "fan-out bug: duplicated relationship rows are not deduplicated and "
        "duplicate cohort rows"
    ),
    strict=True,
)
def test_duplicate_relationship_rows_do_not_fan_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort = pd.DataFrame(
        {
            "ukrdcid": ["B"],
            "sendingfacility": ["RXX"],
            "healthcarefacilitycode": ["RXX01"],
        }
    )
    relationships = _relationships(extra=[("MAIN-SATELLITE", "RXX", "RXX01")])
    result = _run(monkeypatch, cohort, relationships=relationships)

    assert list(result.index).count("B") == 1
