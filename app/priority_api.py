"""HTTP boundary for the independent priority engine."""

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.priority_engine import ComplaintHistory, PriorityEngine, PriorityInput


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/priority", tags=["priority"])
engine = PriorityEngine()

AreaLevel = Literal[
    "single_household_building", "street_lane", "neighborhood",
    "multiple_neighborhoods_major_area",
]
LocationTag = Literal["residential_area", "school", "hospital", "main_road", "transport_hub"]


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["verified", "unavailable"]
    prior_incidents: StrictInt | None = Field(default=None, ge=0)
    duplicate_reports: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent_history(self):
        if self.status == "verified" and self.prior_incidents is None:
            raise ValueError("verified history requires distinct prior_incidents")
        if self.status == "unavailable" and self.prior_incidents is not None:
            raise ValueError("unavailable history cannot provide prior_incidents")
        return self


class PriorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=2000)
    severity: Literal["low", "medium", "high"]
    location: str = Field(min_length=1, max_length=200)
    affected_area: AreaLevel
    days_existing: StrictInt = Field(ge=0)
    location_tags: list[LocationTag] = Field(min_length=1)
    history: HistoryRequest | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("description", "location")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("visible text is required")
        return value


class EmergencyFlag(BaseModel):
    code: str
    explanation: str
    certainty: Literal["possible", "uncertain"]


class PriorityResponse(BaseModel):
    score: float | None
    priority_level: Literal["Low", "Medium", "High", "Critical"] | None
    factor_scores: dict[str, int | None]
    weighted_contributions: dict[str, float | None]
    reported_context: dict[str, Any]
    factor_details: dict[str, dict[str, Any]]
    emergency_flags: list[EmergencyFlag]
    emergency_review_required: bool
    verification_status: Literal["complete", "unverified_history"]
    incomplete_factors: list[str]
    requires_manual_review: bool
    review_reasons: list[str]


@router.post("/calculate", response_model=PriorityResponse)
def calculate_priority(request: PriorityRequest):
    data = PriorityInput(
        description=request.description,
        severity=request.severity,
        location=request.location,
        affected_area=request.affected_area,
        days_existing=request.days_existing,
        location_tags=tuple(request.location_tags),
        history=(
            ComplaintHistory(**request.history.model_dump()) if request.history is not None else None
        ),
    )
    try:
        return engine.calculate(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Priority calculation failed")
        raise HTTPException(status_code=500, detail="Priority calculation failed") from exc
