"""Citizen submission and retrieval endpoints."""

import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.complaint_service import IdempotencyConflict, complaint_detail, complaint_summary, submit_complaint, timestamp_utc
from app.database import get_session
from app.db_models import Complaint, TrackingCredential
from app.priority_api import AreaLevel, LocationTag
from app.security import current_admin, tracking_token, valid_tracking_token, client_key, limit, TrackingUnavailable
from app.db_models import Administrator


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/complaints", tags=["complaints"])
Category = Literal["road_damage", "streetlight", "drainage", "water_leak", "waste"]
Severity = Literal["low", "medium", "high"]
PriorityLevel = Literal["Low", "Medium", "High", "Critical"]
ComplaintStatus = Literal["Submitted", "Under Review", "Assigned", "Resolved"]
ProcessingStatus = Literal["pending", "completed", "incomplete", "failed"]


class ComplaintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    location: str = Field(min_length=1, max_length=200)
    affected_area: AreaLevel
    days_existing: StrictInt = Field(ge=0)
    location_tags: list[LocationTag] = Field(min_length=1)
    related_complaint_id: UUID | None = None

    @field_validator("title", "description", "location")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value

    @field_validator("location_tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class ComplaintSummary(BaseModel):
    id: str
    title: str | None
    description: str
    location: str
    category: Category | None
    severity: Severity | None
    priority_score: float | None
    priority_level: PriorityLevel | None
    status: ComplaintStatus
    classification_status: ProcessingStatus
    priority_status: ProcessingStatus
    processing_status: ProcessingStatus
    emergency_review_required: bool | None
    created_at: str
    updated_at: str


class ComplaintDetail(ComplaintSummary):
    affected_area: AreaLevel
    days_existing: int
    location_tags: list[LocationTag]
    location_tags_verification: Literal["unverified", "verified"]
    normalized_locality: str | None
    requested_related_complaint_id: str | None
    duplicate_of_id: str | None
    category_confidence: float | None
    severity_confidence: float | None
    classification_review: dict[str, Any] | None
    recurrence_prior_incidents: int | None
    recurrence_duplicate_reports: int | None
    recurrence_verification: Literal["unverified", "database_matched"]
    history_metadata: dict[str, Any] | None
    factor_scores: dict[str, int | None] | None
    weighted_contributions: dict[str, float | None] | None
    scoring_breakdown: dict[str, Any] | None
    emergency_flags: list[dict[str, Any]] | None
    review_reasons: list[str] | None
    processing_error_code: str | None


class ComplaintPage(BaseModel):
    items: list[ComplaintSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class TrackingRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)


def citizen_receipt(row: Complaint, token: str) -> dict:
    return {
        "id": row.id, "tracking_token": token, "title": row.title,
        "location": row.location, "created_at": timestamp_utc(row.created_at),
        "status": row.status, "processing_status": row.processing_status,
        "category": row.category, "classification_review": row.classification_review,
        "emergency_review_required": row.emergency_review_required,
    }


@router.post("", status_code=201)
def create_complaint(
    request: ComplaintCreate,
    response: Response,
    session: Session = Depends(get_session),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
):
    try:
        row, created = submit_complaint(session, request.model_dump(mode="json"), idempotency_key)
        if not created:
            response.status_code = 200
            if session.get(TrackingCredential, row.id) is None:
                raise HTTPException(409, detail="Legacy complaint has no tracking credential; contact an administrator")
        response.headers["Cache-Control"] = "no-store"
        return citizen_receipt(row, tracking_token(session, row))
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TrackingUnavailable as exc:
        raise HTTPException(status_code=409, detail="Tracking credential was revoked or rotated; contact an administrator") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        session.rollback()
        logger.exception("Database failure during complaint submission")
        raise HTTPException(status_code=503, detail="Complaint storage is unavailable") from exc
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        logger.exception("Complaint submission failed")
        raise HTTPException(status_code=500, detail="Complaint submission failed") from exc


@router.get("", response_model=ComplaintPage)
def list_complaints(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category: Category | None = None,
    severity: Severity | None = None,
    priority_level: PriorityLevel | None = None,
    status: ComplaintStatus | None = None,
    sort_by: Literal["submitted_at", "priority_score"] = "submitted_at",
    order: Literal["asc", "desc"] = "desc",
    session: Session = Depends(get_session),
    _admin: Administrator = Depends(current_admin),
):
    try:
        conditions = []
        for column, value in (
            (Complaint.category, category), (Complaint.severity, severity),
            (Complaint.priority_level, priority_level), (Complaint.status, status),
        ):
            if value is not None:
                conditions.append(column == value)
        total = session.scalar(select(func.count()).select_from(Complaint).where(*conditions)) or 0
        query = select(Complaint).where(*conditions)
        if sort_by == "priority_score":
            score_order = Complaint.priority_score.asc() if order == "asc" else Complaint.priority_score.desc()
            query = query.order_by(score_order.nulls_last(), Complaint.created_at.desc(), Complaint.id.desc())
        else:
            date_order = Complaint.created_at.asc() if order == "asc" else Complaint.created_at.desc()
            query = query.order_by(date_order, Complaint.id.asc() if order == "asc" else Complaint.id.desc())
        rows = session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
        return {
            "items": [complaint_summary(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }
    except SQLAlchemyError as exc:
        logger.exception("Complaint listing failed")
        raise HTTPException(status_code=503, detail="Complaint storage is unavailable") from exc


@router.post("/{complaint_id}/track")
def track_complaint(complaint_id: UUID, body: TrackingRequest, request: Request, response: Response, session: Session = Depends(get_session)):
    limit(client_key(request, "tracking"), maximum=30)
    row = session.get(Complaint, str(complaint_id))
    if row is None or not valid_tracking_token(session, row.id, body.token):
        raise HTTPException(404, detail="Tracking reference or credential is invalid")
    response.headers["Cache-Control"] = "no-store"
    return {
        "id": row.id, "title": row.title, "status": row.status,
        "processing_status": row.processing_status, "category": row.category,
        "classification_review": row.classification_review,
        "emergency_review_required": row.emergency_review_required,
        "created_at": timestamp_utc(row.created_at), "updated_at": timestamp_utc(row.updated_at),
    }


@router.get("/{complaint_id}", response_model=ComplaintDetail)
def get_complaint(complaint_id: UUID, session: Session = Depends(get_session), _admin: Administrator = Depends(current_admin)):
    try:
        row = session.get(Complaint, str(complaint_id))
        if row is None:
            raise HTTPException(status_code=404, detail="Complaint not found")
        return complaint_detail(row)
    except SQLAlchemyError as exc:
        logger.exception("Complaint retrieval failed")
        raise HTTPException(status_code=503, detail="Complaint storage is unavailable") from exc
