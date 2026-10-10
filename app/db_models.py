"""Persistent complaint and original evaluation record."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Complaint(Base):
    __tablename__ = "complaints"
    __table_args__ = (
        CheckConstraint("days_existing >= 0", name="days_existing_nonnegative"),
        CheckConstraint("priority_score IS NULL OR (priority_score >= 0 AND priority_score <= 100)", name="priority_score_range"),
        CheckConstraint("category_confidence IS NULL OR (category_confidence >= 0 AND category_confidence <= 1)", name="category_confidence_range"),
        CheckConstraint("severity_confidence IS NULL OR (severity_confidence >= 0 AND severity_confidence <= 1)", name="severity_confidence_range"),
        CheckConstraint("status IN ('Submitted', 'Under Review', 'Assigned', 'Resolved')", name="complaint_status_allowed"),
        CheckConstraint("processing_status IN ('pending', 'completed', 'incomplete', 'failed')", name="processing_status_allowed"),
        Index("ix_complaints_history", "category", "locality_key", "status"),
        Index("ix_complaints_submitted", "created_at", "id"),
        Index("ix_complaints_priority", "priority_score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    locality_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    affected_area: Mapped[str] = mapped_column(String(50), nullable=False)
    days_existing: Mapped[int] = mapped_column(Integer, nullable=False)
    location_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    location_tags_verification: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    requested_related_complaint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    duplicate_of_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("complaints.id"), nullable=True)

    category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    category_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_review: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    classification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    priority_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    processing_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    recurrence_prior_incidents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recurrence_duplicate_reports: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recurrence_verification: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    history_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    factor_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    weighted_contributions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    scoring_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    emergency_review_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    emergency_flags: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    review_reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Submitted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class TrackingCredential(Base):
    __tablename__ = "tracking_credentials"
    complaint_id: Mapped[str] = mapped_column(String(36), ForeignKey("complaints.id"), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSecret(Base):
    __tablename__ = "app_secrets"
    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False)


class Administrator(Base):
    __tablename__ = "administrators"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("administrators.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str] = mapped_column(String(36), ForeignKey("complaints.id"), nullable=False, index=True)
    admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("administrators.id"), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class RecommendationReview(Base):
    __tablename__ = "recommendation_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(36), ForeignKey("recommendations.id"), nullable=False, unique=True)
    complaint_id: Mapped[str] = mapped_column(String(36), ForeignKey("complaints.id"), nullable=False, index=True)
    admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("administrators.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class PopulationDecision(Base):
    __tablename__ = "population_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    complaint_id: Mapped[str] = mapped_column(String(36), ForeignKey("complaints.id"), nullable=False, index=True)
    admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("administrators.id"), nullable=False)
    recommendation_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("recommendations.id"))
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    original_score: Mapped[int | None] = mapped_column(Integer)
    proposed_score: Mapped[int | None] = mapped_column(Integer)
    approved_score: Mapped[int | None] = mapped_column(Integer)
    original_priority: Mapped[float | None] = mapped_column(Float)
    proposed_priority: Mapped[float | None] = mapped_column(Float)
    approved_priority: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    available_quantity: Mapped[int | None] = mapped_column(Integer)
    unit_cost: Mapped[float | None] = mapped_column(Float)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    demo_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ResourceAllocation(Base):
    __tablename__ = "resource_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str] = mapped_column(String(36), ForeignKey("complaints.id"), nullable=False, index=True)
    admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("administrators.id"), nullable=False)
    recommendation_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("recommendations.id"))
    request_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    items: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    budget: Mapped[float | None] = mapped_column(Float)
    remarks: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class BudgetPolicy(Base):
    __tablename__ = "budget_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    max_amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class StatusEvent(Base):
    __tablename__ = "status_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str] = mapped_column(String(36), ForeignKey("complaints.id"), nullable=False, index=True)
    admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("administrators.id"), nullable=False)
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DuplicateReview(Base):
    __tablename__ = "duplicate_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str] = mapped_column(String(36), ForeignKey("complaints.id"), nullable=False, unique=True)
    root_id: Mapped[str] = mapped_column(String(36), ForeignKey("complaints.id"), nullable=False)
    admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("administrators.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    complaint_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("complaints.id"), index=True)
    admin_id: Mapped[str] = mapped_column(String(36), ForeignKey("administrators.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
