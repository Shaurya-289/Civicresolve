"""Integration tests for the frontend serving and complaint submission flow."""

from uuid import uuid4
import pytest
from fastapi.testclient import TestClient

from app.database import DB_PATH, get_session, initialize_database, make_engine
from app.main import app
from sqlalchemy.orm import sessionmaker


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_frontend_database(tmp_path):
    """Never let portal tests insert complaints into the persistent project database."""
    path = tmp_path / "portal-test.sqlite3"
    assert path.resolve() != DB_PATH.resolve()
    test_engine = make_engine(path)
    initialize_database(test_engine)
    sessions = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def override_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_session, None)
        test_engine.dispose()


def test_frontend_index_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "Welcome to CivicResolve" in response.text
    assert "Your city. Your voice." in response.text
    assert "Continue as Citizen" in response.text
    assert "Continue as Administrator" in response.text
    citizen = client.get("/citizen")
    assert citizen.status_code == 200
    assert "Report an Issue" in citizen.text
    assert "Track Complaint" in citizen.text


def test_frontend_static_assets_served():
    css_res = client.get("/static/css/styles.css")
    assert css_res.status_code == 200
    assert "--color-primary: #2E7D32" in css_res.text

    app_js_res = client.get("/static/js/app.js")
    assert app_js_res.status_code == 200
    assert "CivicResolve Application Controller" in app_js_res.text

    api_js_res = client.get("/static/js/api.js")
    assert api_js_res.status_code == 200
    assert "submitComplaint" in api_js_res.text

    retry_js_res = client.get("/static/js/retry.js")
    assert retry_js_res.status_code == 200
    assert "retryKeyFor" in retry_js_res.text


def test_frontend_complaint_submission_and_tracking(tmp_path, monkeypatch):
    # Test submission using the exact payload format created by frontend
    idempotency_key = str(uuid4())
    payload = {
        "title": "Severe road cavity near hospital",
        "description": "A very large pothole has opened up near the emergency entrance causing traffic hazard.",
        "location": "Near City Hospital, Outer Ring Road",
        "affected_area": "neighborhood",
        "days_existing": 4,
        "location_tags": ["hospital", "main_road"],
    }
    headers = {"Idempotency-Key": idempotency_key}

    submit_res = client.post("/complaints", json=payload, headers=headers)
    assert submit_res.status_code in (200, 201)
    data = submit_res.json()

    assert "id" in data
    complaint_id = data["id"]
    assert data["title"] == payload["title"]
    assert data["location"] == payload["location"]
    assert data["created_at"].endswith("+00:00")
    assert len(data["tracking_token"]) == 64
    assert data["status"] in ("Submitted", "Under Review", "Assigned", "Resolved")
    assert data["processing_status"] in ("completed", "pending", "incomplete")

    # Re-submitting with identical idempotency key should return same record (idempotent replay)
    replay_res = client.post("/complaints", json=payload, headers=headers)
    assert replay_res.status_code == 200
    assert replay_res.json()["id"] == complaint_id

    # Test tracking lookup
    track_res = client.post(f"/complaints/{complaint_id}/track", json={"token": data["tracking_token"]})
    assert track_res.status_code == 200
    track_data = track_res.json()
    assert track_data["id"] == complaint_id
    assert "description" not in track_data
    assert "location" not in track_data
    assert track_data["status"] == "Submitted"
    assert track_data["created_at"] == data["created_at"]


def test_frontend_complaint_submission_with_null_title():
    # Test submission when title is omitted / null as handled by frontend
    idempotency_key = str(uuid4())
    payload = {
        "title": None,
        "description": "Streetlight fixture is blinking erratically and going dark intermittently.",
        "location": "Lane 4, Green Park Colony",
        "affected_area": "street_lane",
        "days_existing": 2,
        "location_tags": ["residential_area"],
    }
    headers = {"Idempotency-Key": idempotency_key}

    submit_res = client.post("/complaints", json=payload, headers=headers)
    assert submit_res.status_code in (200, 201)
    data = submit_res.json()
    assert data["title"] is None
    assert data["location"] == payload["location"]
    assert data["category"] == "streetlight"
