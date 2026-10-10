"""Conservative, replaceable incident matching against stored complaints."""

import unicodedata

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db_models import Complaint
from app.priority_engine import ComplaintHistory


GENERIC_TOKENS = {"jaipur", "rajasthan", "near", "at", "road", "street", "area", "main", "the", "ke", "pas", "पास"}


def normalize_locality(location: str) -> str | None:
    """Exact-match key only; no geocoding, nearby-landmark inference or fuzzy match."""
    value = unicodedata.normalize("NFKC", location).casefold()
    value = "".join(
        char if char.isalnum() or unicodedata.category(char).startswith("M") else " "
        for char in value
    )
    tokens = value.split()
    while tokens and tokens[0] in {"near", "at", "opposite", "opp", "पास"}:
        tokens.pop(0)
    if len(tokens) < 2 or all(token in GENERIC_TOKENS for token in tokens):
        return None
    return " ".join(tokens)


def derive_history(session: Session, current: Complaint) -> tuple[ComplaintHistory, dict, str | None]:
    """Count resolved primary incidents, or return unverified when active matches are ambiguous."""
    metadata = {
        "method": "exact_normalized_locality_and_predicted_category",
        "scope": "stored_complaints_only",
        "normalized_locality": current.locality_key,
        "matched_resolved_incident_ids": [],
        "unresolved_candidate_ids": [],
        "unclassified_same_area_ids": [],
        "requested_related_complaint_id": current.requested_related_complaint_id,
        "duplicate_relation": None,
        "zero_matches_within_database": False,
    }

    if current.locality_key is None:
        metadata.update(verification_status="unverified", reason="location_not_specific_enough_for_matching")
        return ComplaintHistory(status="unavailable"), metadata, None

    unclassified = session.scalars(
        select(Complaint).where(
            Complaint.id != current.id,
            Complaint.locality_key == current.locality_key,
            Complaint.category.is_(None),
            Complaint.status != "Resolved",
        ).order_by(Complaint.created_at, Complaint.id)
    ).all()
    metadata["unclassified_same_area_ids"] = [row.id for row in unclassified]

    matches = session.scalars(
        select(Complaint).where(
            Complaint.id != current.id,
            Complaint.category == current.category,
            Complaint.locality_key == current.locality_key,
        ).order_by(Complaint.created_at, Complaint.id)
    ).all()
    roots = [row for row in matches if row.duplicate_of_id is None]
    resolved = [row for row in roots if row.status == "Resolved" and row.classification_status == "completed"]
    active = [row for row in roots if row.status != "Resolved"]
    metadata["matched_resolved_incident_ids"] = [row.id for row in resolved]
    metadata["unresolved_candidate_ids"] = [row.id for row in active]

    duplicate_root = None
    if current.requested_related_complaint_id:
        target = session.get(Complaint, current.requested_related_complaint_id)
        if target is not None:
            duplicate_root = session.get(Complaint, target.duplicate_of_id) if target.duplicate_of_id else target
        if (
            duplicate_root is None
            or duplicate_root.id == current.id
            or duplicate_root.category != current.category
            or duplicate_root.locality_key != current.locality_key
            or duplicate_root.status == "Resolved"
        ):
            metadata.update(verification_status="unverified", reason="related_complaint_could_not_be_confirmed")
            return ComplaintHistory(status="unavailable"), metadata, None
        metadata["duplicate_relation"] = "explicit_report_link_unverified"
    elif unclassified:
        metadata.update(verification_status="unverified", reason="unclassified_same_area_report_may_match")
        return ComplaintHistory(status="unavailable"), metadata, None
    elif active:
        # Same category and locality alone cannot prove a report is a duplicate
        # or a new recurrence of an unresolved incident.
        metadata.update(verification_status="unverified", reason="unresolved_same_area_incident_may_be_duplicate")
        return ComplaintHistory(status="unavailable"), metadata, None

    duplicate_count = 0
    if duplicate_root is not None:
        duplicate_count = session.scalar(
            select(func.count()).select_from(Complaint).where(Complaint.duplicate_of_id == duplicate_root.id)
        ) + 1  # include the current report, linked after this calculation

    metadata.update(
        verification_status="database_matched",
        reason="resolved_primary_incidents_counted" if resolved else "zero_matches_within_database",
        zero_matches_within_database=not bool(matches),
        distinct_prior_incidents=len(resolved),
        duplicate_reports_for_linked_incident=duplicate_count,
    )
    return ComplaintHistory(status="verified", prior_incidents=len(resolved), duplicate_reports=duplicate_count), metadata, (
        duplicate_root.id if duplicate_root is not None else None
    )
