"""Independent text-based emergency triage; it never assigns a priority score."""

import re


HAZARDS = {
    "electrical": (
        "Possible electrocution hazard needs immediate human assessment.",
        re.compile(
            r"\b(?:exposed|live)\s+(?:electric(?:al)?\s+)?(?:wire|cable)\b|"
            r"\belectric(?:al)?\s+shock\b|\bcurrent\s+(?:in|through)\s+water\b|"
            r"\bkarant\b|करंट|बिजली का खुला तार|"
            r"\b(?:water|flood\w*)\b.{0,50}\belectric\w*\s+(?:box|cabinet|wire)\b",
            re.IGNORECASE,
        ),
    ),
    "fire_or_smoke": (
        "Fire or smoke may endanger people nearby.",
        re.compile(
            r"\bfire\b(?!\s+(?:station|hydrant|truck|brigade))|"
            r"\bflames?\b|\bburning\b|आग|धुआँ",
            re.IGNORECASE,
        ),
    ),
    "collapse_or_open_hole": (
        "A collapse or open hole may cause serious injury.",
        re.compile(
            r"\b(?:road|bridge|wall)\s+(?:has\s+)?collaps(?:e|ed|ing)\b|"
            r"\bsinkhole\b|\b(?:open|uncovered)\s+(?:storm\s+)?(?:manhole|drain)\b|"
            r"\b(?:manhole|drain)\s+(?:cover\s+)?(?:is\s+)?missing\b|धँस|खुला नाला",
            re.IGNORECASE,
        ),
    ),
    "injury_or_accident": (
        "A reported accident or injury requires human assessment.",
        re.compile(
            r"\b(?:accident|crash|injured|injury|fell|fallen)\b|"
            r"दुर्घटना|हादसा|चोट लगी|गिर गया",
            re.IGNORECASE,
        ),
    ),
    "contamination_or_gas": (
        "Possible contamination or gas exposure requires human assessment.",
        re.compile(
            r"\b(?:sewage|toxic|chemical|biomedical)\s+(?:in|into|mixed\s+with)\s+"
            r"(?:drinking\s+)?water\b|\b(?:gas leak|toxic fumes)\b|"
            r"पीने के पानी में सीवेज|गैस रिसाव",
            re.IGNORECASE,
        ),
    ),
    "unspecified_danger": (
        "The complaint describes a possible danger that needs human assessment.",
        re.compile(r"\b(?:danger|dangerous|hazard|hazardous|life[- ]threatening|unsafe)\b|खतरा|खतरनाक", re.IGNORECASE),
    ),
}

NEGATIONS = {"no", "not", "without", "neither", "nahi", "nahin", "nai", "नहीं", "नही", "बिना"}
UNCERTAINTY = re.compile(
    r"\b(?:maybe|perhaps|possibly|possible|might|may|suspect(?:ed)?|shayad)\b|"
    r"शायद|संभव|लगता|हो सकता",
    re.IGNORECASE,
)
TOKEN = re.compile(r"[\w\u0900-\u097f]+", re.UNICODE)


def _negated(text: str, start: int, end: int) -> bool:
    # Check the local phrase, not the whole sentence: "no fire, but live wire"
    # must still flag the wire. A following Hindi/Hinglish negation is common.
    prefix = re.split(r"[,.;!?।]|\bbut\b|\blekin\b|लेकिन", text[:start], flags=re.I)[-1]
    before = [token.casefold() for token in TOKEN.findall(prefix)][-4:]
    after = [token.casefold() for token in TOKEN.findall(text[end : end + 18])][:2]
    return any(token in NEGATIONS for token in before) or any(token in NEGATIONS for token in after)


def detect_emergencies(description: str) -> list[dict[str, str]]:
    """Return review flags, never a declaration that an unflagged case is safe."""
    flags = []
    for code, (explanation, pattern) in HAZARDS.items():
        for match in pattern.finditer(description):
            if _negated(description, match.start(), match.end()):
                continue
            context = description[max(0, match.start() - 60) : match.end() + 25]
            certainty = "uncertain" if UNCERTAINTY.search(context) else "possible"
            flags.append({"code": code, "explanation": explanation, "certainty": certainty})
            break
    return flags
