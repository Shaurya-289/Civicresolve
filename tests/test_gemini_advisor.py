"""Offline advisory tests: no API key, server, frontend, or project database."""

import copy
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest

from app.priority_engine import ComplaintHistory, PriorityEngine, PriorityInput
from gemini_advisor import GoogleGenAIProvider, ProviderError, RecommendationService
from gemini_advisor.schemas import ResourceConstraints


class MockProvider:
    model_id = "offline-mock"

    def __init__(self, response):
        self.response = response
        self.received = None

    def generate(self, context):
        self.received = context
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def stored_complaint(*, prior_incidents=1, emergency=False):
    description = "Open manhole near school" if emergency else "Streetlight not working for three days"
    history = ComplaintHistory(status="verified", prior_incidents=prior_incidents, duplicate_reports=0) if prior_incidents is not None else ComplaintHistory(status="unavailable")
    evaluation = PriorityEngine().calculate(PriorityInput(
        description=description, severity="medium", location="Mansarovar Jaipur",
        affected_area="street_lane", days_existing=3, location_tags=("residential_area",),
        history=history,
    ))
    return {
        "id": "test-complaint", "title": "Civic issue", "description": description,
        "category": "streetlight", "severity": "medium", "category_confidence": 0.83,
        "severity_confidence": 0.75, "location": "Mansarovar Jaipur",
        "affected_area": "street_lane", "days_existing": 3,
        "location_tags": ["residential_area"], "location_tags_verification": "unverified",
        "recurrence_prior_incidents": prior_incidents, "recurrence_duplicate_reports": 0,
        "recurrence_verification": "database_matched" if prior_incidents is not None else "unverified",
        "history_metadata": {"verification_status": "database_matched" if prior_incidents is not None else "unverified"},
        "factor_scores": evaluation["factor_scores"], "priority_score": evaluation["score"],
        "priority_level": evaluation["priority_level"], "emergency_flags": evaluation["emergency_flags"],
        "review_reasons": evaluation["review_reasons"],
    }


def proposal(score=70):
    return {
        "population": {
            "proposed_score": score, "explanation": "Reported lane serves several households",
            "confidence": "low", "evidence": ["citizen-reported affected area"],
            "assumptions": ["extent not verified"], "requires_human_verification": True,
        },
        "resources": {
            "department": "Public lighting team", "actions": ["Inspect circuit and repair fixture"],
            "workforce": [{"name": "electrician", "quantity": 2}],
            "equipment": [{"name": "ladder", "quantity": 1}],
            "materials": [{"name": "lamp", "quantity": 1}],
            "estimated_days": 2, "explanation": "Inspect before dispatching repairs",
            "limitations": ["Fault cause unknown"],
        },
    }


def run(complaint=None, response=None, constraints=None):
    provider = MockProvider(proposal() if response is None else response)
    result = RecommendationService(provider).recommend(complaint or stored_complaint(), constraints)
    return result.model_dump(mode="json"), provider


def test_successful_proposal_uses_existing_engine_and_preserves_original():
    complaint = stored_complaint()
    before = copy.deepcopy(complaint)
    result, provider = run(complaint)
    revised = result["proposed_revised_evaluation"]
    assert result["review_metadata"]["recommendation_status"] == "ready_for_review"
    assert revised["priority_score"] == complaint["priority_score"] + 7.5
    assert revised["score_difference"] == 7.5
    assert revised["factor_scores"] == {**complaint["factor_scores"], "population": 70}
    assert result["original_evaluation"]["factor_scores"] == complaint["factor_scores"]
    assert result["original_evaluation"]["priority_score"] == complaint["priority_score"]
    assert result["gemini_population_recommendation"]["original_score"] == 40
    assert result["review_metadata"]["requires_human_approval"] is True
    assert complaint == before
    assert provider.received["complaint"]["factor_scores"] == complaint["factor_scores"]


def test_population_decrease():
    result, _ = run(response=proposal(20))
    assert result["proposed_revised_evaluation"]["score_difference"] == -5


def test_declined_population_adjustment_keeps_resources():
    result, _ = run(response=proposal(None))
    assert result["proposed_revised_evaluation"] is None
    assert result["resource_recommendations"]["department"] == "Public lighting team"
    assert "declined" in " ".join(result["review_metadata"]["warnings"])


def test_missing_recurrence_never_produces_numeric_proposal():
    result, _ = run(complaint=stored_complaint(prior_incidents=None))
    assert result["original_evaluation"]["factor_scores"]["recurrence"] is None
    assert result["proposed_revised_evaluation"] is None
    assert result["review_metadata"]["recommendation_status"] == "incomplete_context"


@pytest.mark.parametrize("score", [-1, 101, True, 40.5, "80"])
def test_invalid_population_score_rejected(score):
    result, _ = run(response=proposal(score))
    assert result["review_metadata"]["recommendation_status"] == "invalid_response"
    assert result["gemini_population_recommendation"] is None


@pytest.mark.parametrize("raw", ["not json", "{}", '{"population":{},"population":{}}'])
def test_malformed_or_duplicate_key_response_rejected(raw):
    result, _ = run(response=raw)
    assert result["review_metadata"]["recommendation_status"] == "invalid_response"


@pytest.mark.parametrize("code", ["timeout", "quota_or_rate_limit", "provider_failure"])
def test_provider_failure_returns_structured_unavailable_result(code):
    result, _ = run(response=ProviderError(code))
    assert result["review_metadata"]["recommendation_status"] == "provider_unavailable"
    assert code in result["review_metadata"]["warnings"]
    assert result["original_evaluation"]["factor_scores"]["population"] == 40


def test_unexpected_provider_exception_does_not_escape():
    result, _ = run(response=TimeoutError("request timed out"))
    assert result["review_metadata"]["recommendation_status"] == "provider_unavailable"
    assert result["review_metadata"]["warnings"] == ["provider_failure"]


def test_missing_resource_information_means_no_cost_or_time_claim():
    result, _ = run()
    resources = result["resource_recommendations"]
    assert resources["budget_estimate"] is None
    assert resources["estimated_days"] is None
    assert any("availability" in value for value in resources["limitations"])


def test_costs_from_supplied_inputs_and_budget_limit():
    constraints = {
        "available_workforce": {"electrician": 2}, "equipment_inventory": {"ladder": 1},
        "unit_costs": {"workforce:electrician": "100", "equipment:ladder": "50", "materials:lamp": "20"},
        "currency": "INR", "max_budget": "300", "timeframe_reference_days": 3,
    }
    result, _ = run(constraints=constraints)
    assert result["resource_recommendations"]["budget_estimate"]["amount"] == "270"
    assert result["resource_recommendations"]["estimated_days"] == 2
    constrained = {**constraints, "max_budget": "200"}
    result, _ = run(constraints=constrained)
    assert result["resource_recommendations"] is None
    assert result["review_metadata"]["recommendation_status"] == "constraint_violation"


def test_supplied_inventory_is_enforced_without_silent_clamping():
    result, _ = run(constraints={"available_workforce": {"electrician": 1}})
    assert result["resource_recommendations"] is None
    assert result["review_metadata"]["recommendation_status"] == "constraint_violation"


def test_unknown_cost_cannot_claim_budget_compliance():
    result, _ = run(constraints={"max_budget": "1000", "currency": "INR"})
    assert result["resource_recommendations"]["budget_estimate"] is None
    assert result["review_metadata"]["recommendation_status"] == "needs_manual_constraint_review"


def test_emergency_flags_preserved_and_model_cannot_override():
    complaint = stored_complaint(emergency=True)
    result, _ = run(complaint=complaint)
    assert result["original_evaluation"]["emergency_flags"] == complaint["emergency_flags"]
    malicious = proposal()
    malicious["emergency_flags"] = []
    result, _ = run(complaint=complaint, response=malicious)
    assert result["review_metadata"]["recommendation_status"] == "invalid_response"
    assert result["original_evaluation"]["emergency_flags"] == complaint["emergency_flags"]


def test_baseline_mismatch_prevents_recalculation():
    complaint = stored_complaint()
    complaint["priority_score"] += 3
    result, _ = run(complaint=complaint)
    assert result["proposed_revised_evaluation"] is None
    assert result["review_metadata"]["recommendation_status"] == "incomplete_context"


def test_population_change_without_stated_evidence_is_rejected():
    unsupported = proposal()
    unsupported["population"]["evidence"] = []
    result, _ = run(response=unsupported)
    assert result["review_metadata"]["recommendation_status"] == "invalid_response"


def test_operational_restrictions_require_manual_review():
    result, _ = run(constraints={"restrictions": ["No work during school arrival hours"]})
    assert result["review_metadata"]["recommendation_status"] == "needs_manual_constraint_review"
    assert result["review_metadata"]["requires_human_approval"] is True


def test_google_provider_requires_backend_key_but_offline_service_does_not(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderError) as error:
        GoogleGenAIProvider()
    assert error.value.code == "missing_api_key"
    result, _ = run()
    assert result["review_metadata"]["model_id"] == "offline-mock"


def test_pii_minimization_and_no_frontend_dependency():
    complaint = stored_complaint()
    complaint["description"] += " Call 9876543210 or me@example.org"
    provider = MockProvider(proposal(None))
    RecommendationService(provider).recommend(complaint)
    payload = json.dumps(provider.received)
    assert "9876543210" not in payload and "me@example.org" not in payload
    import sys
    assert not any(name.startswith("frontend") for name in sys.modules)


def test_no_database_modifications_even_when_file_exists():
    with tempfile.TemporaryDirectory(prefix="advisor-test-", dir=Path(__file__).parents[1] / "gemini_advisor") as directory:
        path = Path(directory) / "complaints.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("create table marker (value text)")
                connection.execute("insert into marker values ('original')")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result, _ = run()
        assert result["review_metadata"]["requires_human_approval"] is True
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_invalid_constraints_rejected_before_provider_call():
    provider = MockProvider(proposal())
    with pytest.raises(ValueError):
        RecommendationService(provider).recommend(stored_complaint(), ResourceConstraints.model_validate({"available_workforce": {"electrician": -1}}))
    assert provider.received is None


def test_google_adapter_retries_one_transient_failure_without_sdk_or_key(monkeypatch):
    from gemini_advisor.provider import GoogleGenAIProvider
    monkeypatch.setattr("time.sleep", lambda _: None)

    class Transient(Exception):
        code = 503

    class Models:
        calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise Transient("do not expose internal details")
            return type("Response", (), {"text": '{"ok":true}'})()

    adapter = object.__new__(GoogleGenAIProvider)
    adapter.model_id = "mock-model"
    adapter._client = type("Client", (), {"models": Models()})()
    adapter._types = type("Types", (), {"GenerateContentConfig": staticmethod(lambda **kwargs: kwargs)})()
    assert adapter.generate({"safe": "data"}) == '{"ok":true}'
    assert adapter._client.models.calls == 2


def test_google_adapter_does_not_retry_quota_error():
    from gemini_advisor.provider import GoogleGenAIProvider

    class Quota(Exception):
        code = 429

    class Models:
        calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            raise Quota("sensitive provider text")

    adapter = object.__new__(GoogleGenAIProvider)
    adapter.model_id = "mock-model"
    adapter._client = type("Client", (), {"models": Models()})()
    adapter._types = type("Types", (), {"GenerateContentConfig": staticmethod(lambda **kwargs: kwargs)})()
    with pytest.raises(ProviderError) as exc:
        adapter.generate({"safe": "data"})
    assert exc.value.code == "quota_or_rate_limit"
    assert adapter._client.models.calls == 1
