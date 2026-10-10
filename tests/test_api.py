import csv
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.classifier import CANDIDATE_MODEL, CATEGORY_MODEL, SEVERITY_MODEL, load_models


client = TestClient(app)
VALID_SEVERITIES = {"low", "medium", "high"}


def test_combined_model_is_default_and_cached():
    assert CANDIDATE_MODEL.is_file()
    category, severity = load_models()
    assert load_models()[0] is category
    assert load_models()[1] is severity
    assert set(category.classes_) == {"road_damage", "streetlight", "drainage", "water_leak", "waste"}
    assert set(severity.classes_) == VALID_SEVERITIES


def test_model_does_not_promote_harmless_school_mention():
    response = client.post("/classify", json={"description": "The school lamp timer stays on at noon although the path is bright."})
    assert response.status_code == 200
    assert response.json()["severity"] != "high"


def test_original_models_remain_available_for_rollback(monkeypatch):
    assert CATEGORY_MODEL.is_file() and SEVERITY_MODEL.is_file()
    monkeypatch.setenv("COMPLAINT_MODEL_SOURCE", "legacy")
    load_models.cache_clear()
    try:
        category, severity = load_models()
        assert category.predict(["A deep pothole blocks the street."])[0] == "road_damage"
        assert severity.predict(["A deep pothole blocks the street."])[0] in VALID_SEVERITIES
    finally:
        load_models.cache_clear()


def test_classify_returns_models_predictions():
    response = client.post("/classify", json={"description": "The road has a deep pothole near the school entrance."})
    assert response.status_code == 200
    result = response.json()
    assert result["category"] == "road_damage"
    assert result["severity"] in VALID_SEVERITIES
    assert 0 <= result["category_confidence"] <= 1
    assert 0 <= result["severity_confidence"] <= 1
    assert result["needs_review"] is True
    assert "possible_safety_or_health_hazard" in result["review_reasons"]


def test_blank_description_rejected():
    assert client.post("/classify", json={"description": "   "}).status_code == 422
    assert client.post("/classify", json={}).status_code == 422


def test_oversized_description_rejected():
    assert client.post("/classify", json={"description": "a" * 2001}).status_code == 422


def test_missing_models_returns_service_unavailable(monkeypatch):
    def missing(_):
        raise FileNotFoundError("Trained model files are missing. Run train_models.py first.")

    monkeypatch.setattr("app.main.classify", missing)
    response = client.post("/classify", json={"description": "A streetlight is broken."})
    assert response.status_code == 503
    assert "train_models.py" in response.json()["detail"]


def test_independently_worded_hazards_are_flagged_even_if_label_is_missed():
    novel = Path(__file__).with_name("novel_complaints.csv")
    with novel.open(encoding="utf-8", newline="") as handle:
        high_cases = [row for row in csv.DictReader(handle) if row["severity"] == "high"]
    assert len(high_cases) == 5
    for row in high_cases:
        response = client.post("/classify", json={"description": row["text"]})
        assert response.status_code == 200
        assert response.json()["needs_review"] is True
