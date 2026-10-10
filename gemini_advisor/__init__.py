"""Read-only, optional Gemini advice for a future administrator workflow."""

from .provider import GoogleGenAIProvider, ProviderError
from .service import RecommendationService

__all__ = ["GoogleGenAIProvider", "ProviderError", "RecommendationService"]
