"""API-level offline workflow against a disposable SQLite database."""

from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.admin_api import recommendation_service
from app.database import DB_PATH, get_session, initialize_database, make_engine
from app.db_models import AdminSession, Complaint, Resource, ResourceAllocation, TrackingCredential, utc_now
from app.main import app
from app.security import initialize_admin
from gemini_advisor.service import RecommendationService


class MockProvider:
    model_id = "mock-gemini"

    def __init__(self, population=80):
        self.population = population
        self.received = None

    def generate(self, context):
        self.received = context
        return {
            "population": {
                "proposed_score": self.population, "explanation": "Citizen describes a wider affected area",
                "confidence": "low", "evidence": ["reported area only"], "assumptions": ["unverified"],
                "requires_human_verification": True,
            },
            "resources": {
                "department": "Road maintenance", "actions": ["Inspect and fill cavity"],
                "workforce": [{"name": "crew", "quantity": 2}],
                "equipment": [{"name": "roller", "quantity": 1}],
                "materials": [], "estimated_days": None, "explanation": "Site survey first", "limitations": [],
            },
        }


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "admin.sqlite3"
    assert path.resolve() != DB_PATH.resolve()
    engine = make_engine(path)
    initialize_database(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        with sessions() as session:
            initialize_admin(session, "owner", "test-admin-password-123")
        yield TestClient(app), sessions, path
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def complaint(location="Malviya Nagar Jaipur"):
    return {
        "title": "Dangerous road cavity", "description": "Deep pothole near school caused an accident.",
        "location": location, "affected_area": "street_lane", "days_existing": 3,
        "location_tags": ["school", "main_road"],
    }


def auth(client):
    response = client.post("/admin/auth/login", json={"username": "owner", "password": "test-admin-password-123"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_demo_resources_never_overwrite_existing_inventory(setup):
    client, _, _ = setup
    headers = auth(client)
    assert client.post("/admin/resources/demo").status_code == 401
    assert client.post("/admin/resources/demo", headers=headers).json() == {"added": 3}
    existing = client.get("/admin/resources", headers=headers).json()
    assert len(existing) == 3
    assert all(item["demo_data"] and not item["verified"] for item in existing)
    changed = {**next(item for item in existing if item["id"] == "demo-service-truck"), "available_quantity": 4}
    assert client.put("/admin/resources/demo-service-truck", headers=headers, json=changed).status_code == 200
    assert client.post("/admin/resources/demo", headers=headers).json() == {"added": 0}
    stock = {item["id"]: item["available_quantity"] for item in client.get("/admin/resources", headers=headers).json()}
    assert stock["demo-service-truck"] == 4


def test_admin_frontend_support_endpoints(setup):
    client, _, _ = setup
    assert client.get("/portal/admin/login").status_code == 200
    assert client.get("/portal/admin/dashboard").status_code == 200
    assert client.get("/portal/admin/resources").status_code == 200
    assert client.get(f"/portal/admin/complaints/{uuid4()}/action").status_code == 200
    assert client.get("/admin/summary").status_code == 401
    assert client.get("/admin/resources/budget/policy").status_code == 401
    headers = auth(client)
    assert client.get("/admin/summary", headers=headers).json() == {
        "total": 0, "pending_review": 0, "emergency_review": 0, "resolved": 0,
    }
    assert client.get("/admin/resources/budget/policy", headers=headers).json() == {
        "max_amount": None, "currency": "INR", "verified": False,
    }
    receipt = client.post("/complaints", json=complaint(), headers={"Idempotency-Key": "frontend-summary-001"})
    assert receipt.status_code == 201
    assert client.get("/admin/summary", headers=headers).json() == {
        "total": 1, "pending_review": 1, "emergency_review": 1, "resolved": 0,
    }
    policy = client.put("/admin/resources/budget/policy", headers=headers, json={"max_amount": 10000, "currency": "INR", "verified": True})
    assert policy.status_code == 200
    assert client.get("/admin/resources/budget/policy", headers=headers).json()["max_amount"] == 10000
    second = client.post("/complaints", json=complaint("Another Jaipur lane"), headers={"Idempotency-Key": "frontend-summary-002"})
    assert second.status_code == 201
    first_page = client.get("/admin/complaints", params={"page": 1, "page_size": 1}, headers=headers).json()
    second_page = client.get("/admin/complaints", params={"page": 2, "page_size": 1}, headers=headers).json()
    assert first_page["total"] == 2 and first_page["total_pages"] == 2
    assert first_page["items"][0]["id"] != second_page["items"][0]["id"]


def test_public_privacy_and_admin_sessions(setup):
    client, sessions, _ = setup
    receipt = client.post("/complaints", json=complaint(), headers={"Idempotency-Key": "privacy-test-001"}).json()
    assert "description" not in receipt and "priority_score" not in receipt
    assert client.get("/complaints").status_code == 401
    assert client.get(f"/complaints/{receipt['id']}").status_code == 401
    assert client.get("/admin/complaints").status_code == 401
    assert client.post(f"/complaints/{receipt['id']}/track", json={"token": "0" * 64}).status_code == 404
    tracked = client.post(f"/complaints/{receipt['id']}/track", json={"token": receipt["tracking_token"]})
    assert tracked.status_code == 200
    assert set(tracked.json()).isdisjoint({"description", "location", "severity", "factor_scores", "priority_score"})
    with sessions() as session:
        stored = session.get(TrackingCredential, receipt["id"])
        assert stored.token_hash != receipt["tracking_token"]
    replay = client.post("/complaints", json=complaint(), headers={"Idempotency-Key": "privacy-test-001"})
    assert replay.status_code == 200 and replay.json()["tracking_token"] == receipt["tracking_token"]
    assert client.post("/admin/auth/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/admin/auth/login", json={"username": "absent", "password": "wrong"}).status_code == 401
    assert client.post("/admin/auth/login", json={"username": "owner"}).status_code == 422
    headers = auth(client)
    assert client.get("/admin/auth/me", headers=headers).json()["username"] == "owner"
    assert client.get(f"/admin/complaints/{receipt['id']}", headers=headers).json()["description"] == complaint()["description"]
    assert client.post("/admin/auth/logout", headers=headers).status_code == 204
    assert client.get("/admin/auth/me", headers=headers).status_code == 401
    headers = auth(client)
    token = headers["Authorization"].split()[1]
    from app.security import token_hash
    with sessions() as session:
        row = session.get(AdminSession, token_hash(token))
        row.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert client.get("/admin/auth/me", headers=headers).status_code == 401


def test_tracking_revocation_and_rotation(setup):
    client, _, _ = setup
    receipt = client.post("/complaints", json=complaint(), headers={"Idempotency-Key": "rotate-demo-001"}).json()
    route = f"/complaints/{receipt['id']}/track"
    headers = auth(client)
    assert client.post(f"/admin/complaints/{receipt['id']}/tracking/revoke", headers=headers).status_code == 204
    assert client.post(route, json={"token": receipt["tracking_token"]}).status_code == 404
    assert client.post("/complaints", json=complaint(), headers={"Idempotency-Key": "rotate-demo-001"}).status_code == 409
    rotated = client.post(f"/admin/complaints/{receipt['id']}/tracking/rotate", headers=headers)
    assert rotated.status_code == 200
    assert rotated.json()["tracking_token"] != receipt["tracking_token"]
    assert client.post(route, json={"token": rotated.json()["tracking_token"]}).status_code == 200
    assert client.post(route, json={"token": receipt["tracking_token"]}).status_code == 404


def test_legacy_record_does_not_gain_a_public_tracking_credential(setup):
    client, sessions, _ = setup
    receipt = client.post("/complaints", json=complaint(), headers={"Idempotency-Key": "legacy-demo-001"}).json()
    with sessions() as session:
        session.delete(session.get(TrackingCredential, receipt["id"]))
        session.commit()
    replay = client.post("/complaints", json=complaint(), headers={"Idempotency-Key": "legacy-demo-001"})
    assert replay.status_code == 409
    assert client.post(f"/complaints/{receipt['id']}/track", json={"token": receipt["tracking_token"]}).status_code == 404
    with sessions() as session:
        assert session.get(TrackingCredential, receipt["id"]) is None


def test_full_admin_workflow_with_mock_gemini(setup):
    client, sessions, path = setup
    provider = MockProvider(80)
    app.dependency_overrides[recommendation_service] = lambda: RecommendationService(provider)
    receipt = client.post("/complaints", json=complaint(), headers={"Idempotency-Key": "workflow-001"}).json()
    complaint_id = receipt["id"]
    headers = auth(client)
    queue = client.get("/admin/complaints", headers=headers).json()
    assert queue["total"] == 1 and queue["items"][0]["id"] == complaint_id
    assert "description" not in queue["items"][0]
    original = client.get(f"/admin/complaints/{complaint_id}", headers=headers).json()
    assert original["classification_status"] == "completed"
    assert original["factor_scores"]["population"] == 40
    assert original["priority_score"] is not None
    assert client.get("/admin/complaints", params={"search": "cavity", "emergency_only": True}, headers=headers).json()["total"] == 1
    for resource_id, kind, stock, cost in (("crew", "workforce", 4, 500), ("roller", "equipment", 2, 1000)):
        response = client.put(f"/admin/resources/{resource_id}", headers=headers, json={
            "id": resource_id, "name": resource_id.title(), "kind": kind,
            "available_quantity": stock, "unit_cost": cost, "verified": True, "demo_data": True,
        })
        assert response.status_code == 200, response.text
    assert client.put("/admin/resources/budget/policy", headers=headers, json={"max_amount": 5000, "verified": True}).status_code == 200
    recommendation = client.post(f"/admin/complaints/{complaint_id}/recommendations", headers=headers)
    assert recommendation.status_code == 200, recommendation.text
    rec = recommendation.json()
    assert rec["result"]["review_metadata"]["model_id"] == "mock-gemini"
    assert provider.received["complaint"]["id"] == complaint_id
    assert provider.received["complaint"]["factor_scores"] == original["factor_scores"]
    assert rec["result"]["proposed_revised_evaluation"]["factor_scores"]["population"] == 80
    assert Decimal(rec["result"]["resource_recommendations"]["budget_estimate"]["amount"]) == 2000
    reviewed = client.post(f"/admin/complaints/{complaint_id}/recommendations/{rec['id']}/decision",
                           json={"decision": "accept_for_review", "reason": "Review suggestions separately"}, headers=headers)
    assert reviewed.status_code == 200
    assert client.get(f"/admin/complaints/{complaint_id}", headers=headers).json()["priority_score"] == original["priority_score"]
    action = {"decision": "accept", "recommendation_id": rec["id"], "reason": "Demo review confirmed reported reach"}
    preview = client.post(f"/admin/complaints/{complaint_id}/population/preview", json=action, headers=headers)
    assert preview.status_code == 200
    decision_headers = {**headers, "Idempotency-Key": "population-workflow-001"}
    approved = client.post(f"/admin/complaints/{complaint_id}/population/decisions", json=action, headers=decision_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_score"] == 80
    assert client.post(f"/admin/complaints/{complaint_id}/population/decisions", json=action, headers=decision_headers).json()["id"] == approved.json()["id"]
    current = client.get(f"/admin/complaints/{complaint_id}", headers=headers).json()
    assert current["priority_score"] == original["priority_score"]
    assert current["effective_priority_score"] == preview.json()["approved_evaluation"]["score"]
    allocation = {"decision": "approve", "recommendation_id": rec["id"],
                  "items": [{"resource_id": "crew", "quantity": 1}, {"resource_id": "roller", "quantity": 1}],
                  "approved_budget": 1500, "remarks": "One crew member for initial inspection"}
    allocation_headers = {**headers, "Idempotency-Key": "allocation-workflow-001"}
    approved_allocation = client.post(f"/admin/complaints/{complaint_id}/allocations", json=allocation, headers=allocation_headers)
    assert approved_allocation.status_code == 200, approved_allocation.text
    assert approved_allocation.json()["decision"] == "approved"
    assert client.post(f"/admin/complaints/{complaint_id}/allocations", json=allocation, headers=allocation_headers).json()["id"] == approved_allocation.json()["id"]
    assert {r["id"]: r["available_quantity"] for r in client.get("/admin/resources", headers=headers).json()} == {"roller": 1, "crew": 3}
    for status in ("Under Review", "Assigned", "Resolved"):
        changed = client.post(f"/admin/complaints/{complaint_id}/status", json={"status": status}, headers=headers)
        assert changed.status_code == 200, changed.text
        assert changed.json()["created_at"].endswith("+00:00")
    tracked = client.post(f"/complaints/{complaint_id}/track", json={"token": receipt["tracking_token"]})
    assert tracked.json()["status"] == "Resolved"
    assert len(client.get(f"/admin/complaints/{complaint_id}/status-history", headers=headers).json()) == 3
    assert len(client.get(f"/admin/complaints/{complaint_id}/audit", headers=headers).json()) >= 6
    assert len(client.get(f"/admin/complaints/{complaint_id}/recommendations", headers=headers).json()) == 1
    assert len(client.get(f"/admin/complaints/{complaint_id}/allocations", headers=headers).json()) == 1
    with sessions() as session:
        assert session.get(Complaint, complaint_id).status == "Resolved"
        assert session.scalar(select(ResourceAllocation).where(ResourceAllocation.complaint_id == complaint_id)).decision == "approved"
    reopened = make_engine(path)
    try:
        with sessionmaker(bind=reopened)() as session:
            assert session.get(Complaint, complaint_id).status == "Resolved"
            assert session.get(Resource, "roller").available_quantity == 1
    finally:
        reopened.dispose()


def test_allocation_constraints_and_incomplete_priority(setup):
    client, sessions, _ = setup
    headers = auth(client)
    first = client.post("/complaints", json=complaint(location="Jaipur")).json()
    detail = client.get(f"/admin/complaints/{first['id']}", headers=headers).json()
    assert detail["priority_score"] is None and detail["effective_priority_score"] is None
    assert client.get("/admin/complaints", headers=headers).json()["items"][0]["effective_priority_score"] is None
    manual = {"decision": "manual", "manual_score": 60, "reason": "Manual reach estimate"}
    assert client.post(f"/admin/complaints/{first['id']}/population/preview", json=manual, headers=headers).status_code == 409
    assert client.post(f"/admin/complaints/{first['id']}/status", json={"status": "Resolved"}, headers=headers).status_code == 409
    assert client.post(f"/admin/complaints/{first['id']}/allocations", json={"decision": "approve", "items": [{"resource_id": "missing", "quantity": 1}]}, headers={**headers, "Idempotency-Key": "bad-resource-001"}).status_code == 422
    assert client.post(f"/admin/complaints/{first['id']}/allocations", json={"decision": "approve", "items": [{"resource_id": "crew", "quantity": -1}]}, headers={**headers, "Idempotency-Key": "bad-resource-002"}).status_code == 422
    client.put("/admin/resources/crew", headers=headers, json={"id": "crew", "name": "Crew", "kind": "workforce", "available_quantity": 1, "unit_cost": 100, "verified": True})
    client.put("/admin/resources/budget/policy", headers=headers, json={"max_amount": 200, "verified": True})
    route = f"/admin/complaints/{first['id']}/allocations"
    assert client.post(route, json={"decision": "approve", "items": [{"resource_id": "crew", "quantity": 2}], "approved_budget": 200}, headers={**headers, "Idempotency-Key": "bad-stock-001"}).status_code == 409
    assert client.post(route, json={"decision": "approve", "items": [{"resource_id": "crew", "quantity": 1}], "approved_budget": 300}, headers={**headers, "Idempotency-Key": "bad-budget-001"}).status_code == 409
    assert client.get("/admin/resources", headers=headers).json()[0]["available_quantity"] == 1
    pending = client.post(route, json={"decision": "approve", "items": [{"resource_id": "crew", "quantity": 1}]}, headers={**headers, "Idempotency-Key": "pending-001"})
    assert pending.status_code == 200 and pending.json()["decision"] == "pending_verification"
    assert client.get("/admin/resources", headers=headers).json()[0]["available_quantity"] == 1


def test_missing_gemini_key_does_not_break_citizen_submission(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    headers = auth(client)
    receipt = client.post("/complaints", json=complaint()).json()
    response = client.post(f"/admin/complaints/{receipt['id']}/recommendations", headers=headers)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "missing_api_key"
    assert client.post(f"/complaints/{receipt['id']}/track", json={"token": receipt["tracking_token"]}).status_code == 200


def test_tracking_rate_limit(setup):
    client, _, _ = setup
    receipt = client.post("/complaints", json=complaint()).json()
    from app.security import _attempts
    _attempts.clear()
    route = f"/complaints/{receipt['id']}/track"
    for _ in range(30):
        assert client.post(route, json={"token": "0" * 64}).status_code == 404
    assert client.post(route, json={"token": receipt["tracking_token"]}).status_code == 429
    _attempts.clear()


def test_login_rate_limit(setup):
    client, _, _ = setup
    from app.security import _attempts
    _attempts.clear()
    for _ in range(10):
        assert client.post("/admin/auth/login", json={"username": "owner", "password": "bad"}).status_code == 401
    assert client.post("/admin/auth/login", json={"username": "owner", "password": "bad"}).status_code == 429
    _attempts.clear()


def test_manual_rejection_duplicate_link_and_concurrent_stock(setup):
    client, sessions, _ = setup
    headers = auth(client)
    first = client.post("/complaints", json=complaint()).json()
    second = client.post("/complaints", json=complaint(location="Malviya Nagar, Jaipur")).json()
    duplicate = client.post(f"/admin/complaints/{second['id']}/duplicate-link", headers=headers,
                            json={"root_id": first["id"], "reason": "Administrator verified same physical cavity"})
    assert duplicate.status_code == 200, duplicate.text
    assert client.get(f"/admin/complaints/{second['id']}", headers=headers).json()["duplicate_of_id"] == first["id"]
    assert client.post(f"/admin/complaints/{second['id']}/duplicate-link", headers=headers,
                       json={"root_id": first["id"], "reason": "Different reason"}).status_code == 409
    decision = client.post(f"/admin/complaints/{first['id']}/population/decisions", headers={**headers, "Idempotency-Key": "manual-pop-001"},
                           json={"decision": "manual", "manual_score": 20, "reason": "Narrow area confirmed"})
    assert decision.status_code == 200
    assert decision.json()["approved_priority"] < decision.json()["original_priority"]
    rejection = client.post(f"/admin/complaints/{first['id']}/population/decisions", headers={**headers, "Idempotency-Key": "reject-pop-001"},
                            json={"decision": "reject", "reason": "No new evidence"})
    assert rejection.status_code == 200 and rejection.json()["approved_priority"] is None
    assert client.get(f"/admin/complaints/{first['id']}", headers=headers).json()["effective_priority_score"] == decision.json()["approved_priority"]
    assert client.post(f"/admin/complaints/{first['id']}/population/decisions", headers={**headers, "Idempotency-Key": "manual-pop-001"},
                       json={"decision": "manual", "manual_score": 90, "reason": "Changed"}).status_code == 409
    assert client.put("/admin/resources/crew", headers=headers, json={"id": "crew", "name": "Crew", "kind": "workforce", "available_quantity": 1, "unit_cost": 100, "verified": True}).status_code == 200
    assert client.put("/admin/resources/budget/policy", headers=headers, json={"max_amount": 500, "verified": True}).status_code == 200
    route = f"/admin/complaints/{first['id']}/allocations"

    def reserve(index):
        worker = TestClient(app)
        return worker.post(route, headers={**headers, "Idempotency-Key": f"concurrent-{index:03d}"},
                           json={"decision": "approve", "items": [{"resource_id": "crew", "quantity": 1}], "approved_budget": 100}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(reserve, (1, 2)))
    assert sorted(statuses) == [200, 409]
    assert client.get("/admin/resources", headers=headers).json()[0]["available_quantity"] == 0
    with sessions() as session:
        assert session.query(ResourceAllocation).filter_by(decision="approved").count() == 1
