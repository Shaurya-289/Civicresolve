from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.emergency import detect_emergencies
from app.main import app
from app.priority_engine import ComplaintHistory, PriorityConfig, PriorityEngine, PriorityInput


client = TestClient(app)
engine = PriorityEngine()


def complaint(**changes):
    baseline = PriorityInput(
        description="The public water supply has been disrupted.",
        severity="high",
        location="Citizen-reported area near the hospital",
        affected_area="neighborhood",
        days_existing=10,
        location_tags=("hospital",),
        history=ComplaintHistory(status="verified", prior_incidents=2, duplicate_reports=3),
    )
    return replace(baseline, **changes)


def payload(**changes):
    body = {
        "description": "The public water supply has been disrupted.",
        "severity": "High",
        "location": "Citizen-reported area near the hospital",
        "affected_area": "neighborhood",
        "days_existing": 10,
        "location_tags": ["hospital"],
        "history": {"status": "verified", "prior_incidents": 2, "duplicate_reports": 3},
    }
    body.update(changes)
    return body


def test_weighted_formula_and_normalized_scores():
    result = engine.calculate(complaint())
    assert result["factor_scores"] == {
        "severity": 90, "population": 70, "recurrence": 60,
        "duration": 60, "location": 100,
    }
    assert result["weighted_contributions"] == {
        "severity": 31.5, "population": 17.5, "recurrence": 9.0,
        "duration": 9.0, "location": 10.0,
    }
    assert result["score"] == 77.0
    assert result["priority_level"] == "Critical"
    assert result["emergency_flags"] == []
    assert result["verification_status"] == "complete"
    assert result["emergency_review_required"] is False
    assert result["review_reasons"] == ["high_severity_prediction"]


@pytest.mark.parametrize("label,score", [("low", 20), ("medium", 60), ("high", 90)])
def test_severity_mapping(label, score):
    assert engine.calculate(complaint(severity=label))["factor_scores"]["severity"] == score


@pytest.mark.parametrize("area,score", [
    ("single_household_building", 20), ("street_lane", 40),
    ("neighborhood", 70), ("multiple_neighborhoods_major_area", 100),
])
def test_population_mapping(area, score):
    result = engine.calculate(complaint(affected_area=area))
    assert result["factor_scores"]["population"] == score
    assert result["factor_details"]["population"]["estimated_impact"] is True


@pytest.mark.parametrize("tag,score", [
    ("residential_area", 20), ("main_road", 70), ("transport_hub", 80),
    ("school", 90), ("hospital", 100),
])
def test_location_mapping(tag, score):
    result = engine.calculate(complaint(location_tags=(tag,)))
    assert result["factor_scores"]["location"] == score


def test_multiple_location_tags_use_highest_only():
    result = engine.calculate(complaint(location_tags=("residential_area", "school")))
    assert result["factor_scores"]["location"] == 90
    assert result["factor_details"]["location"]["tags"] == ["residential_area", "school"]


@pytest.mark.parametrize("incidents,score", [(0, 0), (1, 35), (2, 60), (3, 80), (4, 80), (5, 100), (20, 100)])
def test_recurrence_thresholds(incidents, score):
    history = ComplaintHistory(status="verified", prior_incidents=incidents, duplicate_reports=9)
    assert engine.calculate(complaint(history=history))["factor_scores"]["recurrence"] == score


@pytest.mark.parametrize("days,score", [(0, 0), (1, 20), (2, 20), (3, 40), (6, 40), (7, 60), (13, 60), (14, 80), (29, 80), (30, 100)])
def test_duration_thresholds(days, score):
    assert engine.calculate(complaint(days_existing=days))["factor_scores"]["duration"] == score


@pytest.mark.parametrize("score,level", [
    (0, "Low"), (24, "Low"), (25, "Medium"), (49, "Medium"),
    (50, "High"), (74, "High"), (75, "Critical"), (100, "Critical"),
])
def test_priority_level_boundaries(score, level):
    config = PriorityConfig(
        severity_scores={"high": score}, population_scores={"neighborhood": score},
        location_scores={"hospital": score}, recurrence_thresholds=((0, score),),
        duration_thresholds=((0, score),),
    )
    result = PriorityEngine(config).calculate(complaint())
    assert result["score"] == score
    assert result["priority_level"] == level


def test_fractional_score_below_boundary_stays_low():
    config = PriorityConfig(
        severity_scores={"high": 25}, population_scores={"neighborhood": 24},
        location_scores={"hospital": 25}, recurrence_thresholds=((0, 25),),
        duration_thresholds=((0, 25),),
    )
    result = PriorityEngine(config).calculate(complaint())
    assert result["score"] == 24.75
    assert result["priority_level"] == "Low"


def test_unavailable_history_is_not_treated_as_zero():
    for history in (None, ComplaintHistory(status="unavailable")):
        result = engine.calculate(complaint(history=history))
        assert result["score"] is None
        assert result["priority_level"] is None
        assert result["factor_scores"]["recurrence"] is None
        assert result["weighted_contributions"]["recurrence"] is None
        assert result["verification_status"] == "unverified_history"
        assert result["incomplete_factors"] == ["recurrence"]
        assert result["requires_manual_review"] is True


def test_verified_zero_and_duplicate_reports_are_distinct():
    with_duplicates = engine.calculate(complaint(history=ComplaintHistory("verified", 0, 50)))
    without_duplicates = engine.calculate(complaint(history=ComplaintHistory("verified", 0, 0)))
    assert with_duplicates["factor_scores"]["recurrence"] == 0
    assert with_duplicates["score"] == without_duplicates["score"]
    assert with_duplicates["factor_details"]["recurrence"]["duplicate_reports"] == 50


@pytest.mark.parametrize("changes", [
    {"days_existing": -1}, {"days_existing": 2.5}, {"days_existing": True},
    {"location_tags": ()}, {"location_tags": ("unknown",)},
    {"severity": "urgent"}, {"affected_area": "city"},
    {"history": ComplaintHistory("verified")},
    {"history": ComplaintHistory("unavailable", prior_incidents=0)},
])
def test_engine_rejects_invalid_inputs(changes):
    with pytest.raises(ValueError):
        engine.calculate(complaint(**changes))


def test_configurable_mappings_and_invalid_override():
    config = replace(PriorityConfig(), severity_scores={"high": 95})
    assert PriorityEngine(config).calculate(complaint())["factor_scores"]["severity"] == 95
    with pytest.raises(ValueError):
        engine.compare_population_adjustment(complaint(), 101)


def test_population_proposal_changes_only_population_and_score():
    data = complaint()
    comparison = engine.compare_population_adjustment(data, proposed_score=100)
    initial, proposed = comparison["initial"], comparison["proposed"]
    assert initial["score"] == 77.0
    assert proposed["score"] == 84.5
    assert proposed["factor_scores"]["population"] == 100
    for factor in ("severity", "recurrence", "duration", "location"):
        assert proposed["factor_scores"][factor] == initial["factor_scores"][factor]
        assert proposed["weighted_contributions"][factor] == initial["weighted_contributions"][factor]
    assert data.affected_area == "neighborhood"
    assert proposed["factor_details"]["population"]["reported_affected_area"] == "neighborhood"
    assert proposed["factor_details"]["population"]["adjusted_for_proposal"] is True


@pytest.mark.parametrize("description,expected_code", [
    ("A live wire is hanging over the footpath.", "electrical"),
    ("The rubbish pile is on fire near the homes.", "fire_or_smoke"),
    ("There is an open manhole in the road.", "collapse_or_open_hole"),
    ("The uncovered storm drain beside the crossing needs a cover.", "collapse_or_open_hole"),
    ("A cyclist was injured in an accident.", "injury_or_accident"),
    ("There might be a gas leak near the market.", "contamination_or_gas"),
    ("शायद बिजली का खुला तार सड़क पर है।", "electrical"),
])
def test_emergency_hazards_trigger_review(description, expected_code):
    flags = detect_emergencies(description)
    assert expected_code in {flag["code"] for flag in flags}
    assert engine.calculate(complaint(description=description))["emergency_review_required"] is True


def test_uncertain_hazard_is_flagged_as_uncertain():
    flags = detect_emergencies("There might be a gas leak near the market.")
    assert any(flag["code"] == "contamination_or_gas" and flag["certainty"] == "uncertain" for flag in flags)


def test_emergency_flags_do_not_change_numeric_priority():
    baseline = engine.calculate(complaint())
    hazardous = engine.calculate(complaint(description="A live wire is hanging over the footpath."))
    assert hazardous["emergency_review_required"] is True
    assert hazardous["score"] == baseline["score"]
    assert hazardous["priority_level"] == baseline["priority_level"]


@pytest.mark.parametrize("description", [
    "There is no fire and no exposed wire here.",
    "The bus stops beside a fire station.",
    "There are no accidents near the school.",
    "आग नहीं है, केवल कूड़ा पड़ा है।",
])
def test_emergency_false_positives(description):
    assert detect_emergencies(description) == []


def test_negated_hazard_does_not_hide_other_hazard():
    codes = {flag["code"] for flag in detect_emergencies("No fire, but a live wire is exposed.")}
    assert "fire_or_smoke" not in codes
    assert "electrical" in codes


def test_priority_endpoint_returns_breakdown():
    response = client.post("/priority/calculate", json=payload())
    assert response.status_code == 200
    result = response.json()
    assert result["score"] == 77.0
    assert result["priority_level"] == "Critical"
    assert result["factor_scores"]["population"] == 70
    assert result["weighted_contributions"]["population"] == 17.5
    assert result["factor_details"]["location"]["reported_location"] == payload()["location"]
    assert result["reported_context"]["description"] == payload()["description"]


def test_priority_endpoint_incomplete_history():
    missing_history = payload()
    missing_history.pop("history")
    for body in (payload(history={"status": "unavailable"}), missing_history):
        response = client.post("/priority/calculate", json=body)
        assert response.status_code == 200
        result = response.json()
        assert result["score"] is None
        assert result["verification_status"] == "unverified_history"


@pytest.mark.parametrize("changes", [
    {"days_existing": -1}, {"days_existing": 2.5}, {"days_existing": "7"},
    {"location": "  "}, {"location_tags": []}, {"location_tags": ["airport"]},
    {"affected_area": "unknown"}, {"history": {"status": "verified"}},
    {"history": {"status": "unavailable", "prior_incidents": 0}},
])
def test_priority_endpoint_rejects_invalid_data(changes):
    assert client.post("/priority/calculate", json=payload(**changes)).status_code == 422


def test_priority_endpoint_does_not_need_ml_models(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("priority endpoint must not load ML models")

    monkeypatch.setattr("app.classifier.load_models", fail_if_loaded)
    assert client.post("/priority/calculate", json=payload()).status_code == 200
