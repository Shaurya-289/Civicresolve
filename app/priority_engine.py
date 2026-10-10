"""Deterministic priority calculation, independent of HTTP, ML and Gemini."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from app.emergency import detect_emergencies


WEIGHTS = {
    "severity": Decimal("0.35"),
    "population": Decimal("0.25"),
    "recurrence": Decimal("0.15"),
    "duration": Decimal("0.15"),
    "location": Decimal("0.10"),
}


@dataclass(frozen=True)
class PriorityConfig:
    severity_scores: Mapping[str, int] = field(default_factory=lambda: {"low": 20, "medium": 60, "high": 90})
    population_scores: Mapping[str, int] = field(default_factory=lambda: {
        "single_household_building": 20,
        "street_lane": 40,
        "neighborhood": 70,
        "multiple_neighborhoods_major_area": 100,
    })
    location_scores: Mapping[str, int] = field(default_factory=lambda: {
        "residential_area": 20,
        "main_road": 70,
        "transport_hub": 80,
        "school": 90,
        "hospital": 100,
    })
    recurrence_thresholds: tuple[tuple[int, int], ...] = ((0, 0), (1, 35), (2, 60), (3, 80), (5, 100))
    duration_thresholds: tuple[tuple[int, int], ...] = ((0, 0), (1, 20), (3, 40), (7, 60), (14, 80), (30, 100))
    level_thresholds: tuple[int, int, int] = (25, 50, 75)


@dataclass(frozen=True)
class ComplaintHistory:
    status: str  # "verified" or "unavailable"; asserted by the caller
    prior_incidents: int | None = None  # distinct incidents, excluding duplicate reports
    duplicate_reports: int | None = None


@dataclass(frozen=True)
class PriorityInput:
    description: str
    severity: str
    location: str
    affected_area: str
    days_existing: int
    location_tags: tuple[str, ...]
    history: ComplaintHistory | None = None


def _threshold_score(value: int, thresholds: tuple[tuple[int, int], ...]) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Threshold input must be a non-negative integer")
    score = thresholds[0][1]
    for minimum, candidate in thresholds:
        if value >= minimum:
            score = candidate
        else:
            break
    return score


class PriorityEngine:
    def __init__(self, config: PriorityConfig | None = None):
        self.config = config or PriorityConfig()
        self._validate_config()

    def _validate_config(self):
        for mapping in (self.config.severity_scores, self.config.population_scores, self.config.location_scores):
            if not mapping or any(type(value) is not int or not 0 <= value <= 100 for value in mapping.values()):
                raise ValueError("All configured factor scores must be integers from 0 to 100")
        for thresholds in (self.config.recurrence_thresholds, self.config.duration_thresholds):
            if not thresholds or thresholds[0][0] != 0:
                raise ValueError("Thresholds must start at zero")
            if any(type(start) is not int or start < 0 or type(score) is not int or not 0 <= score <= 100 for start, score in thresholds):
                raise ValueError("Threshold entries must contain non-negative starts and 0-100 scores")
            if any(left[0] >= right[0] or left[1] > right[1] for left, right in zip(thresholds, thresholds[1:])):
                raise ValueError("Threshold starts must increase and scores cannot decrease")
        if len(self.config.level_thresholds) != 3 or not (0 < self.config.level_thresholds[0] < self.config.level_thresholds[1] < self.config.level_thresholds[2] <= 100):
            raise ValueError("Priority level thresholds must be three increasing values in 0-100")

    def _validate_input(self, data: PriorityInput):
        if not data.description or not data.description.strip():
            raise ValueError("description is required")
        if not data.location or not data.location.strip():
            raise ValueError("reported location is required")
        if data.severity not in self.config.severity_scores:
            raise ValueError("unknown severity")
        if data.affected_area not in self.config.population_scores:
            raise ValueError("unknown affected area")
        if type(data.days_existing) is not int or data.days_existing < 0:
            raise ValueError("days_existing must be a non-negative integer")
        if not data.location_tags or any(tag not in self.config.location_scores for tag in data.location_tags):
            raise ValueError("at least one supported location tag is required")
        if data.history is not None:
            history = data.history
            if history.status not in {"verified", "unavailable"}:
                raise ValueError("history status must be verified or unavailable")
            if history.status == "verified":
                if type(history.prior_incidents) is not int or history.prior_incidents < 0:
                    raise ValueError("verified history requires non-negative distinct prior_incidents")
            elif history.prior_incidents is not None:
                raise ValueError("unavailable history cannot provide prior_incidents")
            if history.duplicate_reports is not None and (type(history.duplicate_reports) is not int or history.duplicate_reports < 0):
                raise ValueError("duplicate_reports must be non-negative when provided")

    def calculate(self, data: PriorityInput, *, population_override: int | None = None) -> dict:
        self._validate_input(data)
        if population_override is not None and (type(population_override) is not int or not 0 <= population_override <= 100):
            raise ValueError("proposed population score must be an integer from 0 to 100")

        history = data.history
        recurrence = None if history is None or history.status == "unavailable" else _threshold_score(
            history.prior_incidents, self.config.recurrence_thresholds
        )
        location_score = max(self.config.location_scores[tag] for tag in data.location_tags)
        scores = {
            "severity": self.config.severity_scores[data.severity],
            "population": self.config.population_scores[data.affected_area] if population_override is None else population_override,
            "recurrence": recurrence,
            "duration": _threshold_score(data.days_existing, self.config.duration_thresholds),
            "location": location_score,
        }
        decimal_contributions = {
            name: None if score is None else Decimal(score) * WEIGHTS[name]
            for name, score in scores.items()
        }
        contributions = {
            name: None if value is None else float(value)
            for name, value in decimal_contributions.items()
        }
        complete = all(score is not None for score in scores.values())
        total = sum(decimal_contributions.values(), Decimal(0)) if complete else None
        if total is None:
            level = None
        elif total < self.config.level_thresholds[0]:
            level = "Low"
        elif total < self.config.level_thresholds[1]:
            level = "Medium"
        elif total < self.config.level_thresholds[2]:
            level = "High"
        else:
            level = "Critical"

        flags = detect_emergencies(data.description)
        review_reasons = []
        if data.severity == "high":
            review_reasons.append("high_severity_prediction")
        if flags:
            review_reasons.append("possible_emergency_hazard")
        if not complete:
            review_reasons.append("unverified_history")
        return {
            "score": None if total is None else float(total),
            "priority_level": level,
            "factor_scores": scores,
            "weighted_contributions": contributions,
            "reported_context": {
                "description": data.description,
                "location": data.location,
                "affected_area": data.affected_area,
                "location_tags": list(data.location_tags),
            },
            "factor_details": {
                "severity": {"label": data.severity},
                "population": {"reported_affected_area": data.affected_area, "estimated_impact": True,
                               "adjusted_for_proposal": population_override is not None},
                "recurrence": {"history_status": history.status if history else "unavailable",
                               "distinct_prior_incidents": history.prior_incidents if history else None,
                               "duplicate_reports": history.duplicate_reports if history else None},
                "duration": {"days_existing": data.days_existing},
                "location": {"reported_location": data.location, "tags": list(data.location_tags),
                             "scoring_rule": "highest_tag"},
            },
            "emergency_flags": flags,
            "emergency_review_required": bool(flags),
            "verification_status": "complete" if complete else "unverified_history",
            "incomplete_factors": [] if complete else ["recurrence"],
            "requires_manual_review": bool(review_reasons),
            "review_reasons": review_reasons,
        }

    def compare_population_adjustment(self, data: PriorityInput, proposed_score: int) -> dict:
        """Pure comparison for a future admin review; never changes baseline data."""
        return {
            "initial": self.calculate(data),
            "proposed": self.calculate(data, population_override=proposed_score),
        }
