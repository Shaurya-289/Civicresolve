"""Isolated browser demo entry point. Never use this module for production startup."""

from app.admin_api import recommendation_service
from app.main import app
from gemini_advisor.service import RecommendationService


class BrowserDemoProvider:
    model_id = "offline-test-provider"

    def generate(self, _context):
        return {
            "population": {
                "proposed_score": 70,
                "explanation": "The synthetic report describes a school gate used daily.",
                "confidence": "low",
                "evidence": ["Citizen-reported location and usage"],
                "assumptions": ["Footfall has not been measured"],
                "requires_human_verification": True,
            },
            "resources": {
                "department": "Road maintenance",
                "actions": ["Inspect the opening", "Secure and replace the cover"],
                "workforce": [{"name": "crew", "quantity": 2}],
                "equipment": [{"name": "roller", "quantity": 1}],
                "materials": [],
                "estimated_days": None,
                "explanation": "Confirm site conditions before dispatch.",
                "limitations": ["Synthetic provider output for browser verification only"],
            },
        }


app.dependency_overrides[recommendation_service] = lambda: RecommendationService(BrowserDemoProvider())
