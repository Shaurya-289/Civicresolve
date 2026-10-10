"""Authenticated operational API. Original ML and priority snapshots stay immutable."""

import secrets
from decimal import Decimal
from math import isfinite
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.complaint_service import complaint_detail, complaint_summary, timestamp_utc
from app.database import begin_serialized_write, get_session
from app.db_models import (
    Administrator, AuditEvent, BudgetPolicy, Complaint, DuplicateReview, PopulationDecision, TrackingCredential,
    Recommendation, RecommendationReview, Resource, ResourceAllocation, StatusEvent, utc_now,
)
from app.priority_engine import ComplaintHistory, PriorityEngine, PriorityInput
from app.security import current_admin, token_hash
from gemini_advisor.provider import GoogleGenAIProvider, ProviderError
from gemini_advisor.schemas import ResourceConstraints
from gemini_advisor.service import RecommendationService


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(current_admin)])
engine = PriorityEngine()


@router.get("/summary")
def summary(session: Session = Depends(get_session)):
    """Unfiltered dashboard totals, including records outside the current queue page."""
    return {
        "total": session.scalar(select(func.count()).select_from(Complaint)),
        "pending_review": session.scalar(select(func.count()).select_from(Complaint).where(Complaint.status == "Submitted")),
        "emergency_review": session.scalar(select(func.count()).select_from(Complaint).where(Complaint.emergency_review_required.is_(True))),
        "resolved": session.scalar(select(func.count()).select_from(Complaint).where(Complaint.status == "Resolved")),
    }


def audit(session: Session, admin: Administrator, action: str, complaint_id: str | None, details: dict):
    session.add(AuditEvent(id=str(uuid4()), admin_id=admin.id, complaint_id=complaint_id, action=action, details=details))


def required_complaint(session: Session, complaint_id: UUID) -> Complaint:
    row = session.get(Complaint, str(complaint_id))
    if row is None:
        raise HTTPException(404, detail="Complaint not found")
    return row


def latest_decision(session: Session, complaint_id: str):
    return session.scalar(select(PopulationDecision).where(
        PopulationDecision.complaint_id == complaint_id,
        PopulationDecision.approved_priority.is_not(None),
    ).order_by(PopulationDecision.created_at.desc(), PopulationDecision.id.desc()))


def effective_priority(session: Session, row: Complaint) -> dict:
    revision = latest_decision(session, row.id)
    return {
        "effective_priority_score": revision.approved_priority if revision else row.priority_score,
        "effective_priority_level": _level(revision.approved_priority) if revision else row.priority_level,
        "source": "admin_approved_population_revision" if revision else "original_evaluation",
    }


def _level(score: float | None) -> str | None:
    if score is None:
        return None
    if score < 25:
        return "Low"
    if score < 50:
        return "Medium"
    if score < 75:
        return "High"
    return "Critical"


def _serialize_event(row) -> dict:
    values = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        values[column.name] = timestamp_utc(value) if column.name.endswith("_at") else value
    return values


@router.get("/complaints")
def queue(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=100),
    category: str | None = None, severity: str | None = None,
    priority_level: Literal["Low", "Medium", "High", "Critical"] | None = None,
    status: Literal["Submitted", "Under Review", "Assigned", "Resolved"] | None = None,
    processing_status: Literal["pending", "completed", "incomplete", "failed"] | None = None,
    emergency_only: bool = False,
    sort_by: Literal["priority", "submitted_at"] = "priority",
    order: Literal["asc", "desc"] = "desc",
    session: Session = Depends(get_session),
):
    filters = []
    for column, value in (
        (Complaint.category, category), (Complaint.severity, severity),
        (Complaint.status, status), (Complaint.processing_status, processing_status),
    ):
        if value is not None:
            filters.append(column == value)
    if emergency_only:
        filters.append(Complaint.emergency_review_required.is_(True))
    if search:
        term = f"%{search.strip()}%"
        filters.append(or_(Complaint.title.ilike(term), Complaint.description.ilike(term), Complaint.location.ilike(term)))
    # SQLite + a small demo database: sort after applying approved revisions, then paginate.
    rows = session.scalars(select(Complaint).where(*filters)).all()
    pairs = [(row, effective_priority(session, row)) for row in rows]
    if priority_level:
        pairs = [(row, effective) for row, effective in pairs if effective["effective_priority_level"] == priority_level]
    if sort_by == "priority":
        pairs.sort(key=lambda pair: (
            not bool(pair[0].emergency_review_required),
            pair[1]["effective_priority_score"] is None,
            -(pair[1]["effective_priority_score"] or 0) if order == "desc" else (pair[1]["effective_priority_score"] or 0),
            -pair[0].created_at.timestamp(), pair[0].id,
        ))
    else:
        pairs.sort(key=lambda pair: (
            not bool(pair[0].emergency_review_required),
            -pair[0].created_at.timestamp() if order == "desc" else pair[0].created_at.timestamp(), pair[0].id,
        ))
    total = len(pairs)
    selected = pairs[(page - 1) * page_size:page * page_size]
    items = []
    for row, effective in selected:
        summary = complaint_summary(row)
        summary.pop("description")
        items.append({**summary, "description_summary": row.description[:160], **effective})
    return {
        "items": items,
        "page": page, "page_size": page_size, "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/complaints/{complaint_id}")
def detail(complaint_id: UUID, session: Session = Depends(get_session)):
    row = required_complaint(session, complaint_id)
    duplicate_review = session.scalar(select(DuplicateReview).where(DuplicateReview.complaint_id == row.id))
    return {
        **complaint_detail(row), **effective_priority(session, row),
        "recommendations": [_serialize_event(item) for item in session.scalars(select(Recommendation).where(Recommendation.complaint_id == row.id).order_by(Recommendation.created_at)).all()],
        "recommendation_reviews": [_serialize_event(item) for item in session.scalars(select(RecommendationReview).where(RecommendationReview.complaint_id == row.id).order_by(RecommendationReview.created_at)).all()],
        "population_decisions": [_serialize_event(item) for item in session.scalars(select(PopulationDecision).where(PopulationDecision.complaint_id == row.id).order_by(PopulationDecision.created_at)).all()],
        "resource_allocations": [_serialize_event(item) for item in session.scalars(select(ResourceAllocation).where(ResourceAllocation.complaint_id == row.id).order_by(ResourceAllocation.created_at)).all()],
        "status_history": [_serialize_event(item) for item in session.scalars(select(StatusEvent).where(StatusEvent.complaint_id == row.id).order_by(StatusEvent.created_at)).all()],
        "duplicate_review": _serialize_event(duplicate_review) if duplicate_review else None,
    }


def recommendation_service() -> RecommendationService:
    try:
        return RecommendationService(GoogleGenAIProvider())
    except ProviderError as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": "Gemini is not configured"}) from exc


def constraints_from_catalog(session: Session) -> ResourceConstraints:
    resources = session.scalars(select(Resource)).all()
    workforce = {r.id: r.available_quantity for r in resources if r.kind == "workforce" and r.verified and r.available_quantity is not None}
    equipment = {r.id: r.available_quantity for r in resources if r.kind == "equipment" and r.verified and r.available_quantity is not None}
    costs = {f"{r.kind}:{r.id}": Decimal(str(r.unit_cost)) for r in resources if r.verified and r.unit_cost is not None}
    policy = session.get(BudgetPolicy, 1)
    restrictions = [f"Catalog item {r.id}: {r.name} ({r.kind}); {'verified' if r.verified else 'unverified'}; {'demo data' if r.demo_data else 'operational data'}" for r in resources]
    return ResourceConstraints(
        available_workforce=workforce or None, equipment_inventory=equipment or None,
        unit_costs=costs or None, currency="INR" if costs or (policy and policy.verified) else None,
        max_budget=Decimal(str(policy.max_amount)) if policy and policy.verified and policy.max_amount is not None else None,
        restrictions=restrictions,
    )


@router.post("/complaints/{complaint_id}/recommendations")
def generate_recommendation(
    complaint_id: UUID, session: Session = Depends(get_session), admin: Administrator = Depends(current_admin),
    service: RecommendationService = Depends(recommendation_service),
):
    row = required_complaint(session, complaint_id)
    result = service.recommend(row, constraints_from_catalog(session)).model_dump(mode="json")
    record = Recommendation(id=str(uuid4()), complaint_id=row.id, admin_id=admin.id, result=result)
    session.add(record)
    audit(session, admin, "recommendation_requested", row.id, {"recommendation_id": record.id, "status": result["review_metadata"]["recommendation_status"]})
    session.commit()
    return {"id": record.id, "result": result}


@router.get("/complaints/{complaint_id}/recommendations")
def recommendations(complaint_id: UUID, session: Session = Depends(get_session)):
    row = required_complaint(session, complaint_id)
    return [_serialize_event(item) for item in session.scalars(select(Recommendation).where(Recommendation.complaint_id == row.id).order_by(Recommendation.created_at)).all()]


class RecommendationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept_for_review", "reject"]
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/complaints/{complaint_id}/recommendations/{recommendation_id}/decision")
def review_recommendation(
    complaint_id: UUID, recommendation_id: UUID, body: RecommendationAction,
    session: Session = Depends(get_session), admin: Administrator = Depends(current_admin),
):
    row = required_complaint(session, complaint_id)
    recommendation = session.get(Recommendation, str(recommendation_id))
    if recommendation is None or recommendation.complaint_id != row.id:
        raise HTTPException(404, detail="Recommendation not found")
    existing = session.scalar(select(RecommendationReview).where(RecommendationReview.recommendation_id == recommendation.id))
    if existing:
        if existing.decision != body.decision or existing.reason != body.reason:
            raise HTTPException(409, detail="Recommendation already reviewed")
        return _serialize_event(existing)
    review = RecommendationReview(id=str(uuid4()), recommendation_id=recommendation.id,
                                  complaint_id=row.id, admin_id=admin.id, decision=body.decision, reason=body.reason)
    session.add(review)
    audit(session, admin, "recommendation_reviewed", row.id, {"recommendation_id": recommendation.id, "decision": body.decision})
    session.commit()
    return _serialize_event(review)


def _priority_input(row: Complaint) -> PriorityInput:
    history = ComplaintHistory(
        status="verified" if row.recurrence_verification == "database_matched" else "unavailable",
        prior_incidents=row.recurrence_prior_incidents if row.recurrence_verification == "database_matched" else None,
        duplicate_reports=row.recurrence_duplicate_reports,
    )
    return PriorityInput(row.description, row.severity, row.location, row.affected_area, row.days_existing, tuple(row.location_tags), history)


class PopulationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "reject", "manual"]
    recommendation_id: UUID | None = None
    manual_score: int | None = Field(None, ge=0, le=100)
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("manual_score", mode="before")
    @classmethod
    def strict_score(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError("score must be an integer")
        return value


def _population_comparison(session: Session, row: Complaint, body: PopulationAction):
    recommendation = session.get(Recommendation, str(body.recommendation_id)) if body.recommendation_id else None
    if body.recommendation_id and (recommendation is None or recommendation.complaint_id != row.id):
        raise HTTPException(422, detail="Recommendation does not belong to this complaint")
    if recommendation is not None:
        review = session.scalar(select(RecommendationReview).where(RecommendationReview.recommendation_id == recommendation.id))
        if review is not None and review.decision == "reject" and body.decision == "accept":
            raise HTTPException(409, detail="Rejected recommendation cannot be accepted")
    proposed = recommendation.result.get("gemini_population_recommendation", {}).get("proposed_score") if recommendation else None
    if body.decision == "accept" and (recommendation is None or proposed is None):
        raise HTTPException(422, detail="A valid recommendation with a proposed score is required")
    if body.decision == "manual" and body.manual_score is None:
        raise HTTPException(422, detail="manual_score is required")
    if body.decision != "manual" and body.manual_score is not None:
        raise HTTPException(422, detail="manual_score is only valid for a manual decision")
    approved = proposed if body.decision == "accept" else body.manual_score if body.decision == "manual" else None
    if approved is not None and (row.priority_score is None or row.factor_scores is None):
        raise HTTPException(409, detail="Original priority is incomplete; cannot approve a recalculation")
    if approved is not None:
        comparison = engine.compare_population_adjustment(_priority_input(row), approved)
        if comparison["initial"]["factor_scores"] != row.factor_scores or comparison["initial"]["score"] != row.priority_score:
            raise HTTPException(409, detail="Original evaluation differs from current scoring rules; manual review required")
    else:
        comparison = None
    return proposed, approved, comparison


@router.post("/complaints/{complaint_id}/population/preview")
def preview_population(complaint_id: UUID, body: PopulationAction, session: Session = Depends(get_session)):
    row = required_complaint(session, complaint_id)
    proposed, approved, comparison = _population_comparison(session, row, body)
    return {"original_score": (row.factor_scores or {}).get("population"), "proposed_score": proposed,
            "approved_score": approved, "original_priority": row.priority_score,
            "approved_evaluation": comparison["proposed"] if comparison else None}


@router.post("/complaints/{complaint_id}/population/decisions")
def decide_population(
    complaint_id: UUID, body: PopulationAction,
    request_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    session: Session = Depends(get_session), admin: Administrator = Depends(current_admin),
):
    begin_serialized_write(session)
    try:
        existing = session.scalar(select(PopulationDecision).where(PopulationDecision.request_key == request_key))
        if existing:
            if (existing.complaint_id != str(complaint_id) or existing.decision != body.decision
                or existing.recommendation_id != (str(body.recommendation_id) if body.recommendation_id else None)
                or existing.reason != body.reason or (body.decision == "manual" and existing.approved_score != body.manual_score)):
                raise HTTPException(409, detail="Idempotency key already used")
            session.rollback()
            return _serialize_event(existing)
        row = required_complaint(session, complaint_id)
        proposed, approved, comparison = _population_comparison(session, row, body)
        proposal_priority = None
        if proposed is not None and row.priority_score is not None:
            proposal_priority = engine.compare_population_adjustment(_priority_input(row), proposed)["proposed"]["score"]
        record = PopulationDecision(
            id=str(uuid4()), request_key=request_key, complaint_id=row.id, admin_id=admin.id,
            recommendation_id=str(body.recommendation_id) if body.recommendation_id else None,
            decision=body.decision, original_score=(row.factor_scores or {}).get("population"),
            proposed_score=proposed, approved_score=approved, original_priority=row.priority_score,
            proposed_priority=proposal_priority, approved_priority=comparison["proposed"]["score"] if comparison else None,
            reason=body.reason,
        )
        session.add(record)
        audit(session, admin, "population_decision", row.id, {"decision_id": record.id, "decision": body.decision})
        session.commit()
        return _serialize_event(record)
    except Exception:
        session.rollback()
        raise


class ResourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{2,100}$")
    name: str = Field(min_length=1, max_length=150)
    kind: Literal["workforce", "equipment", "materials"]
    available_quantity: int | None = Field(None, ge=0)
    unit_cost: float | None = Field(None, ge=0)
    verified: bool = False
    demo_data: bool = False

    @field_validator("available_quantity", mode="before")
    @classmethod
    def strict_quantity(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError("quantity must be an integer")
        return value

    @field_validator("unit_cost")
    @classmethod
    def finite_cost(cls, value):
        if value is not None and not isfinite(value):
            raise ValueError("cost must be finite")
        return value


@router.get("/resources")
def resources(session: Session = Depends(get_session)):
    return [_serialize_event(row) for row in session.scalars(select(Resource).order_by(Resource.kind, Resource.id)).all()]


@router.post("/resources/demo")
def add_demo_resources(session: Session = Depends(get_session), admin: Administrator = Depends(current_admin)):
    """Insert fixed synthetic items once, without changing any existing catalog row."""
    catalog = (
        ("demo-road-crew", "Road repair crew", "workforce", 10, 900),
        ("demo-service-truck", "Service truck", "equipment", 5, 2500),
        ("demo-repair-kit", "Repair kit", "materials", 20, 700),
    )
    begin_serialized_write(session)
    try:
        added = 0
        for resource_id, name, kind, quantity, cost in catalog:
            if session.get(Resource, resource_id) is not None:
                continue
            session.add(Resource(id=resource_id, name=name, kind=kind, available_quantity=quantity,
                                 unit_cost=cost, verified=False, demo_data=True))
            added += 1
        if added:
            audit(session, admin, "demo_resources_loaded", None, {"added": added})
        session.commit()
        return {"added": added}
    except Exception:
        session.rollback()
        raise


@router.put("/resources/{resource_id}")
def upsert_resource(resource_id: str, body: ResourceInput, session: Session = Depends(get_session), admin: Administrator = Depends(current_admin)):
    if resource_id != body.id:
        raise HTTPException(422, detail="Resource ID mismatch")
    begin_serialized_write(session)
    row = session.get(Resource, resource_id)
    if row is None:
        row = Resource(id=resource_id)
        session.add(row)
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    audit(session, admin, "resource_upsert", None, {"resource_id": resource_id})
    session.commit()
    return _serialize_event(row)


class BudgetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_amount: float | None = Field(None, ge=0)
    currency: Literal["INR"] = "INR"
    verified: bool = False

    @field_validator("max_amount")
    @classmethod
    def finite_amount(cls, value):
        if value is not None and not isfinite(value):
            raise ValueError("budget must be finite")
        return value


@router.get("/resources/budget/policy")
def get_budget(session: Session = Depends(get_session)):
    row = session.get(BudgetPolicy, 1)
    return _serialize_event(row) if row else {"max_amount": None, "currency": "INR", "verified": False}


@router.put("/resources/budget/policy")
def set_budget(body: BudgetInput, session: Session = Depends(get_session), admin: Administrator = Depends(current_admin)):
    begin_serialized_write(session)
    row = session.get(BudgetPolicy, 1)
    if row is None:
        row = BudgetPolicy(id=1)
        session.add(row)
    row.max_amount, row.currency, row.verified = body.max_amount, body.currency, body.verified
    audit(session, admin, "budget_policy_updated", None, {"verified": body.verified})
    session.commit()
    return _serialize_event(row)


class AllocationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str = Field(min_length=2, max_length=100)
    quantity: int = Field(ge=1)

    @field_validator("quantity", mode="before")
    @classmethod
    def integer_quantity(cls, value):
        if type(value) is not int:
            raise ValueError("quantity must be an integer")
        return value


class AllocationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    recommendation_id: UUID | None = None
    items: list[AllocationItem] = Field(default_factory=list)
    approved_budget: float | None = Field(None, ge=0)
    remarks: str | None = Field(None, max_length=1000)

    @field_validator("approved_budget")
    @classmethod
    def finite_budget(cls, value):
        if value is not None and not isfinite(value):
            raise ValueError("budget must be finite")
        return value


@router.post("/complaints/{complaint_id}/allocations")
def allocate(
    complaint_id: UUID, body: AllocationAction,
    request_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    session: Session = Depends(get_session), admin: Administrator = Depends(current_admin),
):
    # Serialize stock and budget checks across concurrent database writers.
    begin_serialized_write(session)
    try:
        existing = session.scalar(select(ResourceAllocation).where(ResourceAllocation.request_key == request_key))
        if existing:
            if (existing.complaint_id != str(complaint_id) or existing.items != [item.model_dump() for item in body.items]
                or existing.budget != body.approved_budget or existing.remarks != body.remarks
                or existing.recommendation_id != (str(body.recommendation_id) if body.recommendation_id else None)
                or (body.decision == "reject") != (existing.decision == "rejected")):
                raise HTTPException(409, detail="Idempotency key already used")
            session.rollback()
            return _serialize_event(existing)
        row = required_complaint(session, complaint_id)
        if body.recommendation_id:
            rec = session.get(Recommendation, str(body.recommendation_id))
            if rec is None or rec.complaint_id != row.id:
                raise HTTPException(422, detail="Recommendation does not belong to complaint")
            review = session.scalar(select(RecommendationReview).where(RecommendationReview.recommendation_id == rec.id))
            if review is not None and review.decision == "reject" and body.decision == "approve":
                raise HTTPException(409, detail="Rejected recommendation cannot be allocated")
        item_ids = [item.resource_id for item in body.items]
        if len(item_ids) != len(set(item_ids)):
            raise HTTPException(422, detail="Duplicate resource IDs in allocation")
        if body.decision == "reject" and body.items:
            raise HTTPException(422, detail="Rejected allocation cannot reserve items")
        resources_by_id = {item.resource_id: session.get(Resource, item.resource_id) for item in body.items}
        if any(resource is None for resource in resources_by_id.values()):
            raise HTTPException(422, detail="Unsupported resource ID")
        for item in body.items:
            resource = resources_by_id[item.resource_id]
            if resource.available_quantity is not None and item.quantity > resource.available_quantity:
                raise HTTPException(409, detail="Insufficient resource stock")
        policy = session.get(BudgetPolicy, 1)
        if policy and policy.verified and policy.max_amount is not None and body.approved_budget is not None and body.approved_budget > policy.max_amount:
            raise HTTPException(409, detail="Requested budget exceeds verified limit")
        all_verified = bool(body.items) and all(
            resource.verified and resource.available_quantity is not None and resource.unit_cost is not None
            for resource in resources_by_id.values()
        )
        cost = sum((Decimal(str(resources_by_id[item.resource_id].unit_cost)) * item.quantity for item in body.items), Decimal(0)) if all_verified else None
        budget_verified = bool(policy and policy.verified and policy.max_amount is not None and body.approved_budget is not None)
        if budget_verified and body.decision == "approve":
            already_committed = session.scalar(select(func.coalesce(func.sum(ResourceAllocation.budget), 0)).where(ResourceAllocation.decision == "approved"))
            if Decimal(str(already_committed)) + Decimal(str(body.approved_budget)) > Decimal(str(policy.max_amount)):
                raise HTTPException(409, detail="Allocation exceeds remaining verified budget")
        if cost is not None and body.approved_budget is not None and cost > Decimal(str(body.approved_budget)):
            raise HTTPException(409, detail="Approved budget is below verified item costs")
        decision = "rejected" if body.decision == "reject" else "approved" if all_verified and budget_verified else "pending_verification"
        if decision == "approved":
            for item in body.items:
                resources_by_id[item.resource_id].available_quantity -= item.quantity
        record = ResourceAllocation(
            id=str(uuid4()), complaint_id=row.id, admin_id=admin.id,
            recommendation_id=str(body.recommendation_id) if body.recommendation_id else None,
            request_key=request_key, decision=decision, items=[item.model_dump() for item in body.items],
            budget=body.approved_budget, remarks=body.remarks,
        )
        session.add(record)
        audit(session, admin, "resource_allocation", row.id, {"allocation_id": record.id, "decision": decision})
        session.commit()
        return _serialize_event(record)
    except Exception:
        session.rollback()
        raise


@router.get("/complaints/{complaint_id}/allocations")
def allocations(complaint_id: UUID, session: Session = Depends(get_session)):
    row = required_complaint(session, complaint_id)
    return [_serialize_event(item) for item in session.scalars(select(ResourceAllocation).where(ResourceAllocation.complaint_id == row.id).order_by(ResourceAllocation.created_at)).all()]


class StatusAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["Under Review", "Assigned", "Resolved"]
    remarks: str | None = Field(None, max_length=1000)


NEXT_STATUS = {"Submitted": "Under Review", "Under Review": "Assigned", "Assigned": "Resolved"}


@router.post("/complaints/{complaint_id}/status")
def change_status(complaint_id: UUID, body: StatusAction, session: Session = Depends(get_session), admin: Administrator = Depends(current_admin)):
    row = required_complaint(session, complaint_id)
    if NEXT_STATUS.get(row.status) != body.status:
        raise HTTPException(409, detail="Invalid workflow transition")
    previous = row.status
    row.status = body.status
    event = StatusEvent(id=str(uuid4()), complaint_id=row.id, admin_id=admin.id,
                        from_status=previous, to_status=body.status, remarks=body.remarks)
    session.add(event)
    audit(session, admin, "status_changed", row.id, {"event_id": event.id, "from": previous, "to": body.status})
    session.commit()
    return _serialize_event(event)


@router.get("/complaints/{complaint_id}/status-history")
def status_history(complaint_id: UUID, session: Session = Depends(get_session)):
    row = required_complaint(session, complaint_id)
    return [_serialize_event(item) for item in session.scalars(select(StatusEvent).where(StatusEvent.complaint_id == row.id).order_by(StatusEvent.created_at)).all()]


@router.get("/complaints/{complaint_id}/audit")
def complaint_audit(complaint_id: UUID, session: Session = Depends(get_session)):
    row = required_complaint(session, complaint_id)
    return [_serialize_event(item) for item in session.scalars(select(AuditEvent).where(AuditEvent.complaint_id == row.id).order_by(AuditEvent.created_at)).all()]


class DuplicateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_id: UUID
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/complaints/{complaint_id}/duplicate-link")
def link_duplicate(complaint_id: UUID, body: DuplicateAction, session: Session = Depends(get_session), admin: Administrator = Depends(current_admin)):
    row = required_complaint(session, complaint_id)
    root = required_complaint(session, body.root_id)
    if (row.id == root.id or root.duplicate_of_id is not None or root.status == "Resolved" or row.status == "Resolved"
        or row.category is None or row.category != root.category
        or row.locality_key is None or row.locality_key != root.locality_key):
        raise HTTPException(422, detail="Reports cannot be confirmed as the same unresolved incident")
    existing = session.scalar(select(DuplicateReview).where(DuplicateReview.complaint_id == row.id))
    if existing:
        if existing.root_id != root.id or existing.reason != body.reason:
            raise HTTPException(409, detail="Duplicate relation already reviewed")
        return _serialize_event(existing)
    row.duplicate_of_id = root.id
    review = DuplicateReview(id=str(uuid4()), complaint_id=row.id, root_id=root.id, admin_id=admin.id, reason=body.reason)
    session.add(review)
    audit(session, admin, "duplicate_link_confirmed", row.id, {"root_id": root.id, "review_id": review.id})
    session.commit()
    return _serialize_event(review)


@router.post("/complaints/{complaint_id}/tracking/revoke", status_code=204)
def revoke_tracking(complaint_id: UUID, session: Session = Depends(get_session), admin: Administrator = Depends(current_admin)):
    row = required_complaint(session, complaint_id)
    credential = session.get(TrackingCredential, row.id)
    if credential is None:
        raise HTTPException(404, detail="Tracking credential not found")
    if credential.revoked_at is None:
        credential.revoked_at = utc_now()
        audit(session, admin, "tracking_revoked", row.id, {})
        session.commit()


@router.post("/complaints/{complaint_id}/tracking/rotate")
def rotate_tracking(complaint_id: UUID, session: Session = Depends(get_session), admin: Administrator = Depends(current_admin)):
    row = required_complaint(session, complaint_id)
    credential = session.get(TrackingCredential, row.id)
    if credential is None:
        raise HTTPException(404, detail="Legacy complaint has no tracking credential; verify identity out of band")
    token = secrets.token_hex(32)
    credential.token_hash = token_hash(token)
    credential.revoked_at = None
    audit(session, admin, "tracking_rotated", row.id, {})
    session.commit()
    return {"id": row.id, "tracking_token": token, "notice": "Shown once; deliver securely after identity verification"}
