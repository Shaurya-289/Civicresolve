"""Admin bearer sessions and opaque citizen tracking capabilities."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_session
from app.db_models import Administrator, AdminSession, AppSecret, Complaint, TrackingCredential, utc_now


bearer = HTTPBearer(auto_error=False)
_attempts = defaultdict(deque)
_lock = threading.Lock()


class TrackingUnavailable(Exception):
    pass


def limit(key: str, *, maximum: int, window: int = 600):
    now = time.monotonic()
    with _lock:
        hits = _attempts[key]
        while hits and hits[0] < now - window:
            hits.popleft()
        if len(hits) >= maximum:
            raise HTTPException(429, detail="Too many attempts; try later", headers={"Retry-After": str(window)})
        hits.append(now)


def client_key(request: Request, purpose: str) -> str:
    # Do not trust X-Forwarded-For without an explicitly configured trusted proxy.
    return f"{purpose}:{request.client.host if request.client else 'unknown'}"


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Administrator password must be at least 12 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def initialize_admin(session: Session, username: str, password: str) -> Administrator:
    username = username.strip().casefold()
    if not username or len(username) > 100:
        raise ValueError("Invalid administrator username")
    if session.query(Administrator).filter_by(username=username).first():
        raise ValueError("Administrator already exists")
    row = Administrator(id=str(uuid4()), username=username, password_hash=hash_password(password))
    session.add(row)
    session.commit()
    return row


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(session: Session, admin: Administrator) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(48)
    expiry = utc_now() + timedelta(hours=8)
    session.add(AdminSession(token_hash=token_hash(token), admin_id=admin.id, expires_at=expiry))
    session.commit()
    return token, expiry


def current_admin(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), session: Session = Depends(get_session)) -> Administrator:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, detail="Administrator authentication required", headers={"WWW-Authenticate": "Bearer"})
    record = session.get(AdminSession, token_hash(credentials.credentials))
    if record is None or record.revoked_at is not None:
        raise HTTPException(401, detail="Invalid or expired session")
    expiry = record.expires_at.replace(tzinfo=timezone.utc) if record.expires_at.tzinfo is None else record.expires_at
    admin = session.get(Administrator, record.admin_id)
    if expiry <= utc_now() or admin is None or not admin.active:
        raise HTTPException(401, detail="Invalid or expired session")
    return admin


def revoke_session(session: Session, token: str):
    record = session.get(AdminSession, token_hash(token))
    if record is not None:
        record.revoked_at = utc_now()
        session.commit()


def tracking_token(session: Session, complaint: Complaint) -> str:
    """Deterministic for safe idempotent replay; DB stores a verifier, not the capability."""
    secret = session.get(AppSecret, "tracking_hmac_v1")
    if secret is None:
        secret = AppSecret(name="tracking_hmac_v1", value=secrets.token_hex(48))
        session.add(secret)
        session.commit()
    token = hmac.new(bytes.fromhex(secret.value), complaint.id.encode(), hashlib.sha256).hexdigest()
    verifier = session.get(TrackingCredential, complaint.id)
    if verifier is None:
        session.add(TrackingCredential(complaint_id=complaint.id, token_hash=token_hash(token)))
        session.commit()
    elif verifier.revoked_at is not None or not hmac.compare_digest(verifier.token_hash, token_hash(token)):
        raise TrackingUnavailable("Tracking credential was revoked or rotated")
    return token


def valid_tracking_token(session: Session, complaint_id: str, token: str) -> bool:
    credential = session.get(TrackingCredential, complaint_id)
    return bool(credential and credential.revoked_at is None and hmac.compare_digest(credential.token_hash, token_hash(token)))
