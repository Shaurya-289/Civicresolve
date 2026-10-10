"""Validated inputs and untrusted Gemini output; no persistence or HTTP coupling."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactorScores(StrictModel):
    severity: int | None = Field(default=None, ge=0, le=100)
    population: int | None = Field(default=None, ge=0, le=100)
    recurrence: int | None = Field(default=None, ge=0, le=100)
    duration: int | None = Field(default=None, ge=0, le=100)
    location: int | None = Field(default=None, ge=0, le=100)

    @field_validator("severity", "population", "recurrence", "duration", "location", mode="before")
    @classmethod
    def integer_or_none(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError("factor scores must be integers")
        return value


class ComplaintSnapshot(StrictModel):
    id: str = Field(min_length=1)
    title: str | None = None
    description: str = Field(min_length=1)
    category: str | None = None
    severity: str | None = None
    category_confidence: float | None = Field(default=None, ge=0, le=1)
    severity_confidence: float | None = Field(default=None, ge=0, le=1)
    location: str = Field(min_length=1)
    affected_area: str
    days_existing: int = Field(ge=0)
    location_tags: list[str]
    location_tags_verification: str = "unverified"
    recurrence_prior_incidents: int | None = Field(default=None, ge=0)
    recurrence_duplicate_reports: int | None = Field(default=None, ge=0)
    recurrence_verification: str = "unverified"
    history_metadata: dict | None = None
    factor_scores: FactorScores | None = None
    priority_score: float | None = Field(default=None, ge=0, le=100)
    priority_level: str | None = None
    emergency_flags: list[dict] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)

    @field_validator("days_existing", "recurrence_prior_incidents", "recurrence_duplicate_reports", mode="before")
    @classmethod
    def integer_or_none(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError("incident counts and days must be integers")
        return value

    @classmethod
    def from_record(cls, record):
        """Copy only allowlisted fields from an ORM row or complaint-detail mapping."""
        source = record if isinstance(record, dict) else vars(record)
        data = {name: source[name] for name in cls.model_fields if name in source}
        return cls.model_validate(data)


class ResourceConstraints(StrictModel):
    available_workforce: dict[str, int] | None = None
    equipment_inventory: dict[str, int] | None = None
    unit_costs: dict[str, Decimal] | None = None
    currency: str | None = None
    max_budget: Decimal | None = Field(default=None, ge=0)
    timeframe_reference_days: int | None = Field(default=None, ge=0)
    restrictions: list[str] = Field(default_factory=list)

    @field_validator("available_workforce", "equipment_inventory")
    @classmethod
    def nonnegative_inventory(cls, value):
        if value is not None and any(type(count) is not int or count < 0 for count in value.values()):
            raise ValueError("inventory counts must be non-negative integers")
        return value

    @field_validator("unit_costs")
    @classmethod
    def nonnegative_costs(cls, value):
        if value is not None and any(not amount.is_finite() or amount < 0 for amount in value.values()):
            raise ValueError("unit costs must be finite non-negative amounts")
        return value

    @model_validator(mode="after")
    def budget_has_currency(self):
        if (self.unit_costs is not None or self.max_budget is not None) and not self.currency:
            raise ValueError("currency is required when costs or budget are supplied")
        return self


class ResourceQuantity(StrictModel):
    name: str = Field(min_length=1)
    quantity: int = Field(ge=0)

    @field_validator("quantity", mode="before")
    @classmethod
    def integer_quantity(cls, value):
        if type(value) is not int:
            raise ValueError("quantity must be an integer")
        return value


class PopulationProposal(StrictModel):
    proposed_score: int | None = Field(ge=0, le=100)
    explanation: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high", "uncertain"]
    evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    requires_human_verification: bool = True

    @field_validator("proposed_score", mode="before")
    @classmethod
    def integer_score(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError("population score must be an integer")
        return value

    @model_validator(mode="after")
    def adjustment_has_evidence(self):
        if self.proposed_score is not None and not self.evidence:
            raise ValueError("a population adjustment requires stated evidence")
        return self


class ResourceProposal(StrictModel):
    department: str = Field(min_length=1)
    actions: list[str] = Field(min_length=1)
    workforce: list[ResourceQuantity] = Field(default_factory=list)
    equipment: list[ResourceQuantity] = Field(default_factory=list)
    materials: list[ResourceQuantity] = Field(default_factory=list)
    estimated_days: int | None = Field(default=None, ge=0)
    explanation: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("estimated_days", mode="before")
    @classmethod
    def integer_days(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError("estimated_days must be an integer")
        return value


class GeminiProposal(StrictModel):
    population: PopulationProposal
    resources: ResourceProposal


class BudgetEstimate(StrictModel):
    amount: Decimal
    currency: str
    basis: str


class ResourceRecommendation(StrictModel):
    department: str
    actions: list[str]
    workforce: list[ResourceQuantity]
    equipment: list[ResourceQuantity]
    materials: list[ResourceQuantity]
    budget_estimate: BudgetEstimate | None
    estimated_days: int | None
    explanation: str
    limitations: list[str]


class RecommendationResult(StrictModel):
    complaint_context: dict
    original_evaluation: dict
    gemini_population_recommendation: dict | None
    proposed_revised_evaluation: dict | None
    resource_recommendations: ResourceRecommendation | None
    review_metadata: dict
