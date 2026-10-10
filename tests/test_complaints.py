from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database import DB_PATH, get_session, initialize_database, make_engine
from app.db_models import Complaint
from app.main import app
from app.recurrence import normalize_locality
from app.security import initialize_admin, issue_session


@pytest.fixture
def complaint_db(tmp_path):
    path = tmp_path / "complaints.sqlite3"
    assert path.resolve() != DB_PATH.resolve()
    test_engine = make_engine(path)
    initialize_database(test_engine)
    sessions = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def override_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with sessions() as session:
            admin = initialize_admin(session, "tester", "temporary-test-password-123")
            token, _ = issue_session(session, admin)
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, sessions, path
    finally:
        app.dependency_overrides.pop(get_session, None)
        test_engine.dispose()


def submission(**changes):
    body = {
        "title": "Damaged road near school",
        "description": "A large pothole has appeared on the main road and vehicles are struggling to pass.",
        "location": "Near ABC School, Jaipur",
        "affected_area": "neighborhood",
        "days_existing": 5,
        "location_tags": ["school", "main_road"],
    }
    body.update(changes)
    return body


def fake_classifier(_text):
    return {
        "category": "road_damage", "severity": "high",
        "category_confidence": 0.91, "severity_confidence": 0.82,
        "needs_review": True, "review_reasons": ["predicted_high_severity"],
    }


def full_record(client, receipt):
    if hasattr(receipt, "json"):
        receipt = receipt.json()
    response = client.get(f"/complaints/{receipt['id']}")
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def fixed_classifier(monkeypatch):
    monkeypatch.setattr("app.complaint_service.classify", fake_classifier)


def test_submission_runs_real_classifier_and_priority(complaint_db):
    client, _, _ = complaint_db
    response = client.post("/complaints", json=submission())
    assert response.status_code == 201
    result = full_record(client, response)
    UUID(result["id"])
    assert result["category"] == "road_damage"
    assert result["severity"] in {"low", "medium", "high"}
    assert result["category_confidence"] is not None
    assert result["classification_status"] == "completed"
    assert result["processing_status"] == "completed"
    assert result["priority_score"] is not None
    assert result["status"] == "Submitted"
    assert result["location_tags_verification"] == "unverified"
    assert result["history_metadata"]["zero_matches_within_database"] is True
    assert result["recurrence_prior_incidents"] == 0
    assert result["factor_scores"]["recurrence"] == 0
    assert result["scoring_breakdown"]["factor_scores"] == result["factor_scores"]


def test_weighted_priority_and_complete_record(complaint_db, fixed_classifier):
    client, _, _ = complaint_db
    response = client.post("/complaints", json=submission())
    assert response.status_code == 201
    item = full_record(client, response)
    assert item["factor_scores"] == {
        "severity": 90, "population": 70, "recurrence": 0,
        "duration": 40, "location": 90,
    }
    assert item["weighted_contributions"] == {
        "severity": 31.5, "population": 17.5, "recurrence": 0.0,
        "duration": 6.0, "location": 9.0,
    }
    assert item["priority_score"] == 64.0
    assert item["priority_level"] == "High"
    assert item["review_reasons"]
    assert item["classification_review"]["needs_review"] is True
    assert item["scoring_breakdown"]["reported_context"]["location"] == item["location"]
    assert item["created_at"] and item["updated_at"]
    assert client.get(f"/complaints/{item['id']}").json() == item


def test_database_persists_across_new_connection(complaint_db, fixed_classifier):
    client, _, path = complaint_db
    complaint_id = client.post("/complaints", json=submission()).json()["id"]
    assert path.exists()
    reopened_engine = make_engine(path)
    try:
        with sessionmaker(bind=reopened_engine)() as session:
            row = session.get(Complaint, complaint_id)
            assert row is not None
            assert row.category == "road_damage"
            assert row.priority_score == 64.0
    finally:
        reopened_engine.dispose()


def test_unique_ids_and_idempotent_retry(complaint_db, fixed_classifier):
    client, _, _ = complaint_db
    first = client.post("/complaints", json=submission(), headers={"Idempotency-Key": "citizen-req-001"})
    replay = client.post("/complaints", json=submission(), headers={"Idempotency-Key": "citizen-req-001"})
    second = client.post("/complaints", json=submission(location="Near XYZ School Jaipur"))
    conflict = client.post("/complaints", json=submission(days_existing=6), headers={"Idempotency-Key": "citizen-req-001"})
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    assert conflict.status_code == 409
    assert client.get("/complaints").json()["total"] == 2


def test_unresolved_match_is_unverified_not_an_automatic_duplicate(complaint_db, fixed_classifier):
    client, _, _ = complaint_db
    first = full_record(client, client.post("/complaints", json=submission()))
    second = full_record(client, client.post("/complaints", json=submission(location="ABC School Jaipur", title="Same area report")))
    assert second["normalized_locality"] == first["normalized_locality"]
    assert second["duplicate_of_id"] is None
    assert second["recurrence_verification"] == "unverified"
    assert second["history_metadata"]["reason"] == "unresolved_same_area_incident_may_be_duplicate"
    assert second["history_metadata"]["unresolved_candidate_ids"] == [first["id"]]
    assert second["factor_scores"]["recurrence"] is None
    assert second["priority_score"] is None
    assert second["priority_status"] == "incomplete"


def test_explicit_duplicate_link_and_resolved_recurrence(complaint_db, fixed_classifier):
    client, sessions, _ = complaint_db
    first = full_record(client, client.post("/complaints", json=submission()))
    duplicate = full_record(client, client.post("/complaints", json=submission(
        location="ABC School Jaipur", related_complaint_id=first["id"], title="Another resident reporting same hole"
    )))
    assert duplicate["duplicate_of_id"] == first["id"]
    assert duplicate["recurrence_prior_incidents"] == 0
    assert duplicate["recurrence_duplicate_reports"] == 1
    assert duplicate["history_metadata"]["duplicate_relation"] == "explicit_report_link_unverified"

    # A future admin workflow will set status; directly update the test DB here.
    with sessions() as session:
        primary = session.get(Complaint, first["id"])
        primary.status = "Resolved"
        session.commit()
    assert client.get("/complaints", params={"status": "Resolved"}).json()["total"] == 1
    next_incident = full_record(client, client.post("/complaints", json=submission(
        location="ABC School Jaipur", title="Fresh road damage after earlier repair"
    )))
    assert next_incident["recurrence_prior_incidents"] == 1
    assert next_incident["factor_scores"]["recurrence"] == 35
    assert next_incident["priority_score"] == 69.25
    assert next_incident["history_metadata"]["matched_resolved_incident_ids"] == [first["id"]]
    assert duplicate["id"] not in next_incident["history_metadata"]["matched_resolved_incident_ids"]


def test_broad_location_makes_history_unverified(complaint_db, fixed_classifier):
    client, _, _ = complaint_db
    result = full_record(client, client.post("/complaints", json=submission(location="Jaipur")))
    assert result["normalized_locality"] is None
    assert result["recurrence_verification"] == "unverified"
    assert result["priority_score"] is None
    assert result["history_metadata"]["reason"] == "location_not_specific_enough_for_matching"


def test_locality_normalization_preserves_hindi_marks():
    assert normalize_locality("वैशाली नगर, जयपुर") == normalize_locality("वैशाली नगर जयपुर")


def test_list_pagination_filtering_sort_and_detail(complaint_db, fixed_classifier):
    client, _, _ = complaint_db
    one = full_record(client, client.post("/complaints", json=submission(location="ABC School Jaipur", affected_area="street_lane")))
    two = full_record(client, client.post("/complaints", json=submission(location="XYZ School Jaipur", affected_area="multiple_neighborhoods_major_area")))
    three = full_record(client, client.post("/complaints", json=submission(location="QRS School Jaipur", affected_area="neighborhood")))
    page = client.get("/complaints", params={"page": 1, "page_size": 2}).json()
    assert page["total"] == 3
    assert page["total_pages"] == 2
    assert len(page["items"]) == 2
    assert client.get("/complaints", params={"page": 2, "page_size": 2}).json()["items"][0]["id"] == one["id"]
    assert client.get("/complaints", params={"category": "road_damage", "severity": "high", "status": "Submitted"}).json()["total"] == 3
    assert client.get("/complaints", params={"priority_level": "Critical"}).json()["total"] == 0
    sorted_items = client.get("/complaints", params={"sort_by": "priority_score", "order": "desc"}).json()["items"]
    assert [item["id"] for item in sorted_items] == [two["id"], three["id"], one["id"]]
    assert client.get(f"/complaints/{three['id']}").json()["factor_scores"]["population"] == 70
    assert client.get(f"/complaints/{__import__('uuid').uuid4()}").status_code == 404


def test_priority_sort_keeps_incomplete_scores_last(complaint_db, fixed_classifier):
    client, _, _ = complaint_db
    scored = full_record(client, client.post("/complaints", json=submission()))
    incomplete = full_record(client, client.post("/complaints", json=submission(title="Unresolved area match")))
    assert incomplete["priority_score"] is None
    for order in ("asc", "desc"):
        items = client.get("/complaints", params={"sort_by": "priority_score", "order": order}).json()["items"]
        assert [item["id"] for item in items] == [scored["id"], incomplete["id"]]


@pytest.mark.parametrize("changes", [
    {"description": "  "}, {"location": ""}, {"days_existing": -1},
    {"days_existing": 2.5}, {"location_tags": []}, {"location_tags": ["moon"]},
    {"affected_area": "unknown"}, {"related_complaint_id": "not-a-uuid"},
])
def test_invalid_complaints_rejected_without_insert(complaint_db, changes):
    client, _, _ = complaint_db
    assert client.post("/complaints", json=submission(**changes)).status_code == 422
    assert client.get("/complaints").json()["total"] == 0


def test_nonexistent_related_complaint_rejected(complaint_db, fixed_classifier):
    client, _, _ = complaint_db
    body = submission(related_complaint_id="00000000-0000-4000-8000-000000000001")
    assert client.post("/complaints", json=body).status_code == 422
    assert client.get("/complaints").json()["total"] == 0


def test_classifier_failure_keeps_submitted_record(complaint_db, monkeypatch):
    client, _, _ = complaint_db

    def broken(_):
        raise RuntimeError("private server detail")

    monkeypatch.setattr("app.complaint_service.classify", broken)
    response = client.post("/complaints", json=submission(description="An exposed wire is near the path."))
    assert response.status_code == 201
    result = full_record(client, response)
    assert result["status"] == "Submitted"
    assert result["processing_status"] == "failed"
    assert result["classification_status"] == "failed"
    assert result["processing_error_code"] == "classification_failed"
    assert result["category"] is None
    assert result["priority_score"] is None
    assert result["emergency_review_required"] is True
    assert "private server detail" not in response.text
    assert client.get(f"/complaints/{result['id']}").json()["processing_status"] == "failed"


def test_missing_model_artifact_preserves_complaint(complaint_db, monkeypatch):
    client, _, _ = complaint_db

    def missing(_):
        raise FileNotFoundError("model path details")

    monkeypatch.setattr("app.complaint_service.classify", missing)
    result = full_record(client, client.post("/complaints", json=submission()))
    assert result["processing_error_code"] == "classification_unavailable"
    assert result["classification_status"] == "failed"
    assert "model path details" not in str(result)


def test_unclassified_same_area_record_prevents_false_zero_history(complaint_db, monkeypatch):
    client, _, _ = complaint_db

    def broken(_):
        raise RuntimeError("classifier unavailable")

    monkeypatch.setattr("app.complaint_service.classify", broken)
    failed = full_record(client, client.post("/complaints", json=submission()))
    monkeypatch.setattr("app.complaint_service.classify", fake_classifier)
    next_report = full_record(client, client.post("/complaints", json=submission(title="Second citizen report")))
    assert next_report["history_metadata"]["unclassified_same_area_ids"] == [failed["id"]]
    assert next_report["history_metadata"]["reason"] == "unclassified_same_area_report_may_match"
    assert next_report["priority_score"] is None


def test_priority_failure_keeps_classification(complaint_db, fixed_classifier, monkeypatch):
    client, _, _ = complaint_db

    def broken(_):
        raise RuntimeError("sensitive scoring detail")

    monkeypatch.setattr("app.complaint_service.priority_engine.calculate", broken)
    response = client.post("/complaints", json=submission())
    assert response.status_code == 201
    result = full_record(client, response)
    assert result["category"] == "road_damage"
    assert result["classification_status"] == "completed"
    assert result["priority_status"] == "failed"
    assert result["processing_error_code"] == "priority_failed"
    assert result["priority_score"] is None
    assert "sensitive scoring detail" not in response.text


def test_existing_endpoints_still_work(complaint_db):
    client, _, _ = complaint_db
    assert client.post("/classify", json={"description": "A deep pothole is near the school."}).status_code == 200
    body = {
        "description": "Road damage has disrupted the lane.", "severity": "medium",
        "location": "ABC School Jaipur", "affected_area": "street_lane",
        "days_existing": 2, "location_tags": ["main_road"],
        "history": {"status": "verified", "prior_incidents": 0},
    }
    assert client.post("/priority/calculate", json=body).status_code == 200
