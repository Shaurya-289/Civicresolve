"""Advisory-only orchestration. No sessions, writes, API routes, or ML calls."""

import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import ValidationError

from app.priority_engine import ComplaintHistory, PriorityEngine, PriorityInput

from .provider import ProviderError, ProposalProvider
from .schemas import (
    BudgetEstimate, ComplaintSnapshot, GeminiProposal, RecommendationResult,
    ResourceConstraints, ResourceRecommendation,
)


_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+91[ -]?)?[6-9]\d{9}(?!\d)")


def _minimize_personal_data(value: str | None) -> str | None:
    if value is None:
        return None
    return _PHONE.sub("[phone omitted]", _EMAIL.sub("[email omitted]", value))


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_proposal(raw: str | dict) -> GeminiProposal:
    if isinstance(raw, str):
        raw = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    return GeminiProposal.model_validate(raw)


def _priority_input(complaint: ComplaintSnapshot) -> PriorityInput:
    if complaint.recurrence_verification == "database_matched" and complaint.recurrence_prior_incidents is not None:
        history = ComplaintHistory(
            status="verified",
            prior_incidents=complaint.recurrence_prior_incidents,
            duplicate_reports=complaint.recurrence_duplicate_reports,
        )
    else:
        history = ComplaintHistory(status="unavailable")
    return PriorityInput(
        description=complaint.description,
        severity=complaint.severity,
        location=complaint.location,
        affected_area=complaint.affected_area,
        days_existing=complaint.days_existing,
        location_tags=tuple(complaint.location_tags),
        history=history,
    )


def _matching_baseline(complaint: ComplaintSnapshot, initial: dict) -> bool:
    if complaint.factor_scores is None or complaint.priority_score is None:
        return False
    return (
        initial["factor_scores"] == complaint.factor_scores.model_dump()
        and initial["score"] is not None
        and abs(initial["score"] - complaint.priority_score) < 0.000001
        and initial["priority_level"] == complaint.priority_level
    )


def _resource_result(proposal, constraints: ResourceConstraints) -> ResourceRecommendation:
    limitations = list(proposal.limitations)
    if constraints.restrictions:
        limitations.append("Operational restrictions require manual confirmation: " + "; ".join(constraints.restrictions))

    for label, items, stock in (
        ("workforce", proposal.workforce, constraints.available_workforce),
        ("equipment", proposal.equipment, constraints.equipment_inventory),
    ):
        if stock is None:
            limitations.append(f"{label} availability was not supplied or verified")
            continue
        requested = {}
        for item in items:
            requested[item.name] = requested.get(item.name, 0) + item.quantity
        if any(count > stock.get(name, 0) for name, count in requested.items()):
            raise ValueError(f"{label} proposal exceeds supplied availability")

    budget = None
    items = (
        [("workforce", item) for item in proposal.workforce]
        + [("equipment", item) for item in proposal.equipment]
        + [("materials", item) for item in proposal.materials]
    )
    if constraints.unit_costs is not None and constraints.currency and all(
        f"{kind}:{item.name}" in constraints.unit_costs for kind, item in items
    ) and items:
        amount = sum(
            (constraints.unit_costs[f"{kind}:{item.name}"] * item.quantity for kind, item in items),
            Decimal(0),
        )
        if constraints.max_budget is not None and amount > constraints.max_budget:
            raise ValueError("estimated cost exceeds supplied maximum budget")
        budget = BudgetEstimate(
            amount=amount, currency=constraints.currency,
            basis="supplied per-unit costs multiplied by proposed quantities; advisory only",
        )
    else:
        limitations.append("Budget estimate unavailable: complete per-unit costs were not supplied")
        if constraints.max_budget is not None:
            limitations.append("Compliance with supplied budget cannot be verified")

    estimated_days = proposal.estimated_days if constraints.timeframe_reference_days is not None else None
    if estimated_days is None:
        limitations.append("Resolution timeframe unavailable without an operational reference")
    else:
        limitations.append("Timeframe is advisory and based on supplied operational reference")
    return ResourceRecommendation(
        department=proposal.department, actions=proposal.actions,
        workforce=proposal.workforce, equipment=proposal.equipment, materials=proposal.materials,
        budget_estimate=budget, estimated_days=estimated_days,
        explanation=proposal.explanation, limitations=limitations,
    )


class RecommendationService:
    def __init__(self, provider: ProposalProvider, priority_engine: PriorityEngine | None = None):
        self.provider = provider
        self.priority_engine = priority_engine or PriorityEngine()

    def recommend(self, complaint_record, constraints: ResourceConstraints | dict | None = None) -> RecommendationResult:
        """Read a stored complaint snapshot and return proposals; never persist or approve them."""
        complaint = ComplaintSnapshot.from_record(complaint_record)
        resources = ResourceConstraints.model_validate(constraints or {})
        original_factors = complaint.factor_scores.model_dump() if complaint.factor_scores else {
            key: None for key in ("severity", "population", "recurrence", "duration", "location")
        }
        result = {
            "complaint_context": {
                "id": complaint.id, "category": complaint.category,
                "severity": complaint.severity, "location": complaint.location,
                "affected_area": complaint.affected_area,
                "location_tags_verification": complaint.location_tags_verification,
            },
            "original_evaluation": {
                "factor_scores": original_factors,
                "priority_score": complaint.priority_score,
                "priority_level": complaint.priority_level,
                "emergency_flags": complaint.emergency_flags,
                "review_reasons": complaint.review_reasons,
            },
            "gemini_population_recommendation": None,
            "proposed_revised_evaluation": None,
            "resource_recommendations": None,
            "review_metadata": {
                "model_id": self.provider.model_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "recommendation_status": "pending",
                "requires_human_approval": True,
                "warnings": [],
            },
        }
        metadata = result["review_metadata"]
        payload = {
            "complaint": {
                "id": complaint.id, "title": _minimize_personal_data(complaint.title),
                "description": _minimize_personal_data(complaint.description),
                "category": complaint.category, "severity": complaint.severity,
                "category_confidence": complaint.category_confidence,
                "severity_confidence": complaint.severity_confidence,
                "reported_location": _minimize_personal_data(complaint.location),
                "reported_affected_area": complaint.affected_area,
                "reported_location_tags": complaint.location_tags,
                "location_tags_verification": complaint.location_tags_verification,
                "days_existing": complaint.days_existing,
                "recurrence_verification": complaint.recurrence_verification,
                "recurrence_prior_incidents": complaint.recurrence_prior_incidents,
                "recurrence_duplicate_reports": complaint.recurrence_duplicate_reports,
                "history_metadata": complaint.history_metadata,
                "factor_scores": original_factors,
                "original_priority_score": complaint.priority_score,
                "original_priority_level": complaint.priority_level,
                "emergency_flags": complaint.emergency_flags,
                "review_reasons": complaint.review_reasons,
            },
            "resource_constraints": resources.model_dump(mode="json"),
        }
        try:
            raw = self.provider.generate(payload)
        except ProviderError as exc:
            metadata["recommendation_status"] = "provider_unavailable"
            metadata["warnings"].append(exc.code)
            return RecommendationResult.model_validate(result)
        except Exception:
            metadata["recommendation_status"] = "provider_unavailable"
            metadata["warnings"].append("provider_failure")
            return RecommendationResult.model_validate(result)
        try:
            proposal = _parse_proposal(raw)
        except (ValidationError, ValueError, TypeError):
            metadata["recommendation_status"] = "invalid_response"
            metadata["warnings"].append("Gemini response failed schema/JSON validation")
            return RecommendationResult.model_validate(result)

        population = proposal.population
        result["gemini_population_recommendation"] = {
            "original_score": original_factors["population"],
            "proposed_score": population.proposed_score,
            "explanation": population.explanation,
            "confidence": population.confidence,
            "evidence": population.evidence,
            "assumptions": population.assumptions,
            "requires_human_verification": population.requires_human_verification,
        }
        metadata["recommendation_status"] = "ready_for_review"

        if population.proposed_score is not None:
            try:
                data = _priority_input(complaint)
                comparison = self.priority_engine.compare_population_adjustment(data, population.proposed_score)
                if not _matching_baseline(complaint, comparison["initial"]):
                    metadata["warnings"].append("Stored baseline is incomplete or differs from current deterministic scoring")
                elif comparison["proposed"]["score"] is not None:
                    proposed = comparison["proposed"]
                    result["proposed_revised_evaluation"] = {
                        "priority_score": proposed["score"],
                        "priority_level": proposed["priority_level"],
                        "factor_scores": proposed["factor_scores"],
                        "weighted_contributions": proposed["weighted_contributions"],
                        "changed_factor": "population",
                        "score_difference": round(proposed["score"] - complaint.priority_score, 6),
                        "explanation": "Only the population-impact factor changed; proposal requires human approval",
                    }
            except (ValueError, TypeError):
                metadata["warnings"].append("Required scoring context is invalid or unavailable")
        else:
            metadata["warnings"].append("Gemini declined population adjustment")

        try:
            result["resource_recommendations"] = _resource_result(proposal.resources, resources)
        except ValueError as exc:
            metadata["warnings"].append(str(exc))
            metadata["recommendation_status"] = "constraint_violation"

        budget_unverified = resources.max_budget is not None and (
            result["resource_recommendations"] is not None
            and result["resource_recommendations"].budget_estimate is None
        )
        if (resources.restrictions or budget_unverified) and metadata["recommendation_status"] == "ready_for_review":
            metadata["recommendation_status"] = "needs_manual_constraint_review"

        if result["proposed_revised_evaluation"] is None and population.proposed_score is not None:
            metadata["recommendation_status"] = "incomplete_context" if metadata["recommendation_status"] == "ready_for_review" else metadata["recommendation_status"]
        return RecommendationResult.model_validate(result)
