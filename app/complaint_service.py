"""Submission orchestration: persistence, ML, database history, then pure scoring."""

import hashlib
import json
import logging
from datetime import timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.classifier import classify
from app.db_models import Complaint
from app.emergency import detect_emergencies
from app.priority_engine import PriorityEngine, PriorityInput
from app.recurrence import derive_history, normalize_locality


logger = logging.getLogger(__name__)
priority_engine = PriorityEngine()
VALID_CATEGORIES = {"road_damage", "streetlight", "drainage", "water_leak", "waste"}
VALID_SEVERITIES = {"low", "medium", "high"}


class IdempotencyConflict(Exception):
    pass


def _fingerprint(data: dict) -> str:
    canonical = dict(data)
    canonical["location_tags"] = sorted(set(canonical["location_tags"]))
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _existing_for_key(session: Session, key: str, fingerprint: str) -> Complaint | None:
    existing = session.scalar(select(Complaint).where(Complaint.idempotency_key == key))
    if existing is not None and existing.request_fingerprint != fingerprint:
        raise IdempotencyConflict("Idempotency-Key was already used for a different complaint")
    return existing


def submit_complaint(session: Session, data: dict, idempotency_key: str | None) -> tuple[Complaint, bool]:
    fingerprint = _fingerprint(data)
    if idempotency_key:
        existing = _existing_for_key(session, idempotency_key, fingerprint)
        if existing is not None:
            return existing, False

    related_id = data.get("related_complaint_id")
    if related_id:
        related = session.get(Complaint, related_id)
        if related is None or related.status == "Resolved":
            raise ValueError("related_complaint_id must reference an unresolved complaint")

    row = Complaint(
        id=str(uuid4()),
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        title=data.get("title"),
        description=data["description"],
        location=data["location"],
        locality_key=normalize_locality(data["location"]),
        affected_area=data["affected_area"],
        days_existing=data["days_existing"],
        location_tags=data["location_tags"],
        location_tags_verification="unverified",
        requested_related_complaint_id=related_id,
        recurrence_verification="unverified",
        status="Submitted",
        classification_status="pending",
        priority_status="pending",
        processing_status="pending",
    )
    try:
        session.add(row)
        session.commit()  # durable before attempting fallible ML or scoring
    except IntegrityError:
        session.rollback()
        if idempotency_key:
            existing = _existing_for_key(session, idempotency_key, fingerprint)
            if existing is not None:
                return existing, False
        raise

    try:
        prediction = classify(row.description)
        if prediction["category"] not in VALID_CATEGORIES or prediction["severity"] not in VALID_SEVERITIES:
            raise ValueError("Classifier returned an unsupported label")
        row.category = prediction["category"]
        row.severity = prediction["severity"]
        row.category_confidence = prediction.get("category_confidence")
        row.severity_confidence = prediction.get("severity_confidence")
        row.classification_review = {
            "needs_review": bool(prediction.get("needs_review", False)),
            "review_reasons": list(prediction.get("review_reasons", [])),
        }
        row.classification_status = "completed"
        session.commit()  # retain successful classification if scoring later fails
    except Exception as exc:
        logger.exception("Classification failed for complaint %s", row.id)
        session.rollback()
        row = session.get(Complaint, row.id)
        row.classification_status = "failed"
        row.priority_status = "pending"
        row.processing_status = "failed"
        row.processing_error_code = "classification_unavailable" if isinstance(exc, FileNotFoundError) else "classification_failed"
        row.emergency_flags = detect_emergencies(row.description)
        row.emergency_review_required = bool(row.emergency_flags)
        row.review_reasons = ["classification_failed"] + (["possible_emergency_hazard"] if row.emergency_flags else [])
        session.commit()
        return row, True

    try:
        history, metadata, duplicate_of_id = derive_history(session, row)
        priority_input = PriorityInput(
            description=row.description,
            severity=row.severity,
            location=row.location,
            affected_area=row.affected_area,
            days_existing=row.days_existing,
            location_tags=tuple(row.location_tags),
            history=history,
        )
        evaluation = priority_engine.calculate(priority_input)
        row.duplicate_of_id = duplicate_of_id
        row.recurrence_prior_incidents = history.prior_incidents
        row.recurrence_duplicate_reports = history.duplicate_reports
        row.recurrence_verification = metadata["verification_status"]
        row.history_metadata = metadata
        row.factor_scores = evaluation["factor_scores"]
        row.weighted_contributions = evaluation["weighted_contributions"]
        row.priority_score = evaluation["score"]
        row.priority_level = evaluation["priority_level"]
        row.scoring_breakdown = evaluation  # immutable original snapshot for future proposals
        row.emergency_review_required = evaluation["emergency_review_required"]
        row.emergency_flags = evaluation["emergency_flags"]
        row.review_reasons = list(dict.fromkeys(
            row.classification_review["review_reasons"]
            + evaluation["review_reasons"]
            + ["location_tags_unverified"]
        ))
        row.priority_status = "completed" if evaluation["score"] is not None else "incomplete"
        row.processing_status = row.priority_status
        session.commit()
        return row, True
    except Exception:
        logger.exception("Priority processing failed for complaint %s", row.id)
        session.rollback()
        row = session.get(Complaint, row.id)
        row.priority_status = "failed"
        row.processing_status = "failed"
        row.processing_error_code = "priority_failed"
        row.recurrence_verification = "unverified"
        row.history_metadata = {"verification_status": "unverified", "reason": "processing_failed"}
        row.emergency_flags = detect_emergencies(row.description)
        row.emergency_review_required = bool(row.emergency_flags)
        row.review_reasons = ["priority_failed", "location_tags_unverified"] + (
            ["possible_emergency_hazard"] if row.emergency_flags else []
        )
        session.commit()
        return row, True


def timestamp_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def complaint_summary(row: Complaint) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "location": row.location,
        "category": row.category,
        "severity": row.severity,
        "priority_score": row.priority_score,
        "priority_level": row.priority_level,
        "status": row.status,
        "classification_status": row.classification_status,
        "priority_status": row.priority_status,
        "processing_status": row.processing_status,
        "emergency_review_required": row.emergency_review_required,
        "created_at": timestamp_utc(row.created_at),
        "updated_at": timestamp_utc(row.updated_at),
    }


def complaint_detail(row: Complaint) -> dict:
    return {
        **complaint_summary(row),
        "affected_area": row.affected_area,
        "days_existing": row.days_existing,
        "location_tags": row.location_tags,
        "location_tags_verification": row.location_tags_verification,
        "normalized_locality": row.locality_key,
        "requested_related_complaint_id": row.requested_related_complaint_id,
        "duplicate_of_id": row.duplicate_of_id,
        "category_confidence": row.category_confidence,
        "severity_confidence": row.severity_confidence,
        "classification_review": row.classification_review,
        "recurrence_prior_incidents": row.recurrence_prior_incidents,
        "recurrence_duplicate_reports": row.recurrence_duplicate_reports,
        "recurrence_verification": row.recurrence_verification,
        "history_metadata": row.history_metadata,
        "factor_scores": row.factor_scores,
        "weighted_contributions": row.weighted_contributions,
        "scoring_breakdown": row.scoring_breakdown,
        "emergency_flags": row.emergency_flags,
        "review_reasons": row.review_reasons,
        "processing_error_code": row.processing_error_code,
    }
