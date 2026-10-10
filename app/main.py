"""API for complaint classification and independent priority calculation."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.classifier import classify
from app.priority_api import router as priority_router
from app.complaints_api import router as complaints_router
from app.admin_auth_api import router as admin_auth_router
from app.admin_api import router as admin_router
from app.database import engine, initialize_database, verify_database_ready


logger = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if engine.dialect.name == "postgresql":
        verify_database_ready()
    else:
        initialize_database()
    yield


app = FastAPI(title="Urban Infrastructure Complaint Service", version="0.3.0", lifespan=lifespan)
app.include_router(priority_router)
app.include_router(complaints_router)
app.include_router(admin_auth_router)
app.include_router(admin_router)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def serve_role_selection():
        return FileResponse(FRONTEND_DIR / "welcome.html")

    @app.get("/citizen", include_in_schema=False)
    @app.get("/report", include_in_schema=False)
    @app.get("/track", include_in_schema=False)
    def serve_frontend():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/portal/admin/login", include_in_schema=False)
    @app.get("/portal/admin/dashboard", include_in_schema=False)
    @app.get("/portal/admin/resources", include_in_schema=False)
    @app.get("/portal/admin/complaints/{complaint_id}", include_in_schema=False)
    @app.get("/portal/admin/complaints/{complaint_id}/action", include_in_schema=False)
    def serve_admin(complaint_id: str | None = None):
        return FileResponse(FRONTEND_DIR / "admin.html")



class ComplaintRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)

    @field_validator("description")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("description must contain visible text")
        return value


class ClassificationResponse(BaseModel):
    category: str
    severity: str
    category_confidence: float
    severity_confidence: float
    needs_review: bool
    review_reasons: list[str]


@app.post("/classify", response_model=ClassificationResponse)
def classify_complaint(request: ComplaintRequest):
    try:
        return classify(request.description)
    except FileNotFoundError as exc:
        logger.error("Classification models unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Complaint classification failed")
        raise HTTPException(status_code=500, detail="Classification failed") from exc
