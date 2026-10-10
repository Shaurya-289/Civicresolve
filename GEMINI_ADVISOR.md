# Gemini admin advisor (admin-triggered component)

The underlying `gemini_advisor` package remains **advisory and read-only**. The authenticated `POST /admin/complaints/{id}/recommendations` route now invokes it on explicit admin request and persists its output for review. It is never called during citizen complaint submission. It cannot change the official priority, emergency flag, status, worker assignment, or budget; separate authenticated human decisions are required. See [README.md](README.md) for the full workflow.

## Files and dependency

- `gemini_advisor/schemas.py`: validated complaint snapshot, resource constraints, Gemini proposal, and result.
- `gemini_advisor/provider.py`: injectable provider interface and optional Google `google-genai` adapter.
- `gemini_advisor/service.py`: context minimization, validation, deterministic population-only comparison, and resource checks.
- `tests/test_gemini_advisor.py`: offline mock tests.

The supported [`google-genai` Python SDK](https://googleapis.github.io/python-genai/) is listed in `requirements.txt`. Install the project requirements before using live Gemini. Offline mock tests and importing `gemini_advisor` work without the SDK or a key. Live compatibility has not been verified in this environment.

Set `GEMINI_API_KEY` only in the backend process environment; never expose it in a browser or checked-in file. `GEMINI_MODEL` optionally overrides the default `gemini-2.5-flash-lite`. The adapter uses one logical structured-JSON request with a 20-second per-attempt timeout; it retries a transient timeout/server failure once after 0.4 seconds, but does not retry quota/rate-limit errors. The default model should be rechecked for availability and price in the deployment account before a live rollout. No live call was made for this implementation.

## Offline use and shape

```python
from gemini_advisor import RecommendationService

class MockProvider:
    model_id = "offline-mock"
    def generate(self, context: dict) -> dict:
        return {
            "population": {
                "proposed_score": 70,
                "explanation": "The reported lane may affect more households than the original area band suggests.",
                "confidence": "low",
                "evidence": ["citizen-reported lane impact"],
                "assumptions": ["extent is unverified"],
                "requires_human_verification": True,
            },
            "resources": {
                "department": "Public lighting team",
                "actions": ["Inspect circuit and repair fixture"],
                "workforce": [{"name": "electrician", "quantity": 2}],
                "equipment": [{"name": "ladder", "quantity": 1}],
                "materials": [{"name": "lamp", "quantity": 1}],
                "estimated_days": None,
                "explanation": "Inspect first to confirm the fault.",
                "limitations": ["Cause and stock are unverified"],
            },
        }

# complaint may be an existing Complaint ORM row or complaint_detail(row) mapping.
result = RecommendationService(MockProvider()).recommend(complaint)
payload = result.model_dump(mode="json")
```

`recommend` accepts only selected complaint fields (ID, text, classification, reported location/area, recorded factors and history, emergency flags, etc.). It does not serialize the full ORM object. Common phone numbers and email addresses are redacted from the title, description, and location sent to Gemini; this is **not** comprehensive PII detection. The context labels citizen statements and location tags as unverified.

For the synthetic stored complaint in the tests, the original factors are severity `60`, population `40`, recurrence `35`, duration `40`, and location `20`. The original score is `44.25` (Medium). A proposed population score of `70` yields `51.75` (High) through the existing `PriorityEngine.compare_population_adjustment`; the returned proposal changes only population and has a `+7.5` score difference. The official stored score remains `44.25`; a separate authenticated admin decision may set an effective priority without overwriting it.

The response sections are `complaint_context`, `original_evaluation`, `gemini_population_recommendation`, `proposed_revised_evaluation`, `resource_recommendations`, and `review_metadata`. `review_metadata.requires_human_approval` is always `true`. A malformed Gemini response, timeout, quota error, or inventory/budget violation produces a structured status and warning rather than changing records. Missing or mismatched baseline factors prevent a numeric recalculation. No adjustment leaves `proposed_revised_evaluation` null.

Optional resource constraints can be passed as a `ResourceConstraints` instance or mapping:

```python
constraints = {
    "available_workforce": {"electrician": 2},
    "equipment_inventory": {"ladder": 1},
    "unit_costs": {
        "workforce:electrician": "100",  # cost per proposed worker for the job
        "equipment:ladder": "50",       # cost per proposed equipment unit
        "materials:lamp": "20",         # cost per proposed material unit
    },
    "currency": "INR",
    "max_budget": "300",
    "timeframe_reference_days": 3,
    "restrictions": ["No work during school arrival hours"],
}
```

The service computes money only from **all** supplied per-unit costs and proposed quantities; otherwise the budget estimate is unavailable. It rejects proposals exceeding supplied workforce, equipment, or maximum budget. Free-text operational restrictions cannot be mechanically proven; they force a manual-constraint-review status. If inventory is absent, availability remains explicitly unverified. A timeframe is omitted unless an operational reference is supplied; even then it is advisory. No resource is reserved or allocated.

## Current admin integration

The authenticated recommendation route reads the stored complaint, supplies the current catalog and budget constraints, invokes `RecommendationService` once, saves its advisory result in `recommendations`, and audits the request. The `population/preview` and `population/decisions` routes recalculate through the original deterministic priority engine, preserving the original snapshot. Recommendation review and resource allocation are separate human actions. Admins should verify external-provider data handling and operational facts before using a live key.

Run offline tests with `python -B -m pytest -q tests/test_gemini_advisor.py -p no:cacheprovider` from the project root. They require neither internet nor `GEMINI_API_KEY`.

## Limitations

The service checks structure and numeric constraints, not real-world truth. Model explanations, named departments, suggested actions, worker counts, and time estimates remain unverified advice. It cannot establish actual population, nearby facilities, stock, prices, local policy compliance, or whether an unsafe operational recommendation is hidden in natural language. A human must review emergency flags independently. One mock-backed test suite is not a live Gemini integration test.
