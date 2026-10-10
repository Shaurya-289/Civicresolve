"""Administrator login and revocable bearer sessions."""

from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import get_session
from app.db_models import Administrator, AuditEvent
from app.security import bearer, client_key, current_admin, issue_session, limit, revoke_session, verify_password


router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(body: Login, request: Request, response: Response, session: Session = Depends(get_session)):
    limit(client_key(request, "admin-login"), maximum=10)
    admin = session.query(Administrator).filter_by(username=body.username.strip().casefold()).first()
    if admin is None or not admin.active or not verify_password(body.password, admin.password_hash):
        raise HTTPException(401, detail="Invalid administrator credentials")
    token, expires_at = issue_session(session, admin)
    session.add(AuditEvent(id=str(uuid4()), admin_id=admin.id, action="login", details={"session_expires_at": expires_at.isoformat()}))
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token, "token_type": "bearer", "expires_at": expires_at.isoformat()}


@router.post("/logout", status_code=204)
def logout(credentials: HTTPAuthorizationCredentials = Depends(bearer), admin: Administrator = Depends(current_admin), session: Session = Depends(get_session)):
    revoke_session(session, credentials.credentials)
    session.add(AuditEvent(id=str(uuid4()), admin_id=admin.id, action="logout", details={}))
    session.commit()


@router.get("/me")
def me(admin: Administrator = Depends(current_admin)):
    return {"id": admin.id, "username": admin.username}
