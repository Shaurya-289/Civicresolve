"""Load pre-trained complaint classifiers and identify cases needing human review."""

from functools import lru_cache
from pathlib import Path
import os
import re

import joblib
from sklearn.pipeline import Pipeline


MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
CANDIDATE_MODEL = MODEL_DIR / "complaint_model (1).pkl"
CATEGORY_MODEL = MODEL_DIR / "category.joblib"
SEVERITY_MODEL = MODEL_DIR / "severity.joblib"
EXPECTED_CATEGORIES = {"road_damage", "streetlight", "drainage", "water_leak", "waste"}
EXPECTED_SEVERITIES = {"low", "medium", "high"}

# Probability cutoffs are review heuristics, not calibrated probabilities or
# severity scores. High predictions and explicit hazard language always flag.
REVIEW_CONFIDENCE = 0.60
HAZARD_WORDS = re.compile(
    r"\b(?:accident|crash|injur\w*|fell|fall|school|hospital|clinic|"
    r"fire|burning|smoke|shock|electri\w*|exposed|collapse|sinkhole|"
    r"hazard|danger\w*|unsafe|sewage|flood\w*|children|child|"
    r"gir\w*|bach\w*|khatr\w*|aag|karant|current|aspatal)\b|"
    r"दुर्घटना|हादसा|चोट|गिर|स्कूल|अस्पताल|बच्च|आग|धुआँ|करंट|खतरा|"
    r"सीवेज|गंदा पानी|धँस|फिसल",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def load_models():
    """Load the trusted teammate artifact once per worker; original files stay for rollback."""
    source = os.environ.get("COMPLAINT_MODEL_SOURCE", "combined").casefold()
    if source == "legacy":
        if not CATEGORY_MODEL.is_file() or not SEVERITY_MODEL.is_file():
            raise FileNotFoundError("Original trained model files are missing")
        return joblib.load(CATEGORY_MODEL), joblib.load(SEVERITY_MODEL)
    if source != "combined":
        raise ValueError("COMPLAINT_MODEL_SOURCE must be combined or legacy")
    if not CANDIDATE_MODEL.is_file():
        raise FileNotFoundError("Trained model files are missing. Run train_models.py first.")
    saved = joblib.load(CANDIDATE_MODEL)
    if not isinstance(saved, dict) or set(saved) != {"category", "severity"}:
        raise ValueError("Combined model must contain category and severity pipelines")
    category_model, severity_model = saved["category"], saved["severity"]
    for model, expected in ((category_model, EXPECTED_CATEGORIES), (severity_model, EXPECTED_SEVERITIES)):
        if not isinstance(model, Pipeline) or not hasattr(model, "predict_proba"):
            raise ValueError("Combined model contains an unsupported classifier")
        if {_normalize_label(label) for label in model.classes_} != expected:
            raise ValueError("Combined model labels do not match the API taxonomy")
    return category_model, severity_model


def _normalize_label(label: object) -> str:
    return str(label).strip().casefold().replace(" ", "_").replace("-", "_")


def classify(text: str) -> dict:
    category_model, severity_model = load_models()
    category = _normalize_label(category_model.predict([text])[0])
    severity = _normalize_label(severity_model.predict([text])[0])
    category_confidence = float(category_model.predict_proba([text]).max())
    severity_confidence = float(severity_model.predict_proba([text]).max())

    reasons = []
    if category_confidence < REVIEW_CONFIDENCE:
        reasons.append("uncertain_category")
    if category_confidence < 0.40:
        reasons.append("possible_unsupported_category")
    if severity_confidence < REVIEW_CONFIDENCE:
        reasons.append("uncertain_severity")
    if severity == "high":
        reasons.append("predicted_high_severity")
    if HAZARD_WORDS.search(text):
        reasons.append("possible_safety_or_health_hazard")

    return {
        "category": category,
        "severity": severity,
        "category_confidence": round(category_confidence, 4),
        "severity_confidence": round(severity_confidence, 4),
        "needs_review": bool(reasons),
        "review_reasons": reasons,
    }
