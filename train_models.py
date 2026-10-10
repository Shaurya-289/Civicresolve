"""Train once, evaluate holdout and wording-shift sets, then save model artifacts."""

import csv
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    precision_recall_fscore_support,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "civic_complaints_900.csv"
NOVEL = ROOT / "tests" / "novel_complaints.csv"
MODELS = ROOT / "models"
LABELS = {
    "category": ["road_damage", "streetlight", "drainage", "water_leak", "waste"],
    "severity": ["low", "medium", "high"],
}
LOCALITIES = [
    "Malviya Nagar Sector 3", "Mansarovar Sector 7", "Vaishali Nagar",
    "Bagru main road", "Jagatpura", "Tonk Road", "C-Scheme", "Jhotwara",
    "Vidhyadhar Nagar", "Pratap Nagar", "Sodala", "Amer Road", "Bapu Nagar",
    "Sanganer", "Raja Park", "Civil Lines", "Muralipura", "Sitapura Industrial Area",
    "Shastri Nagar", "Adarsh Nagar", "Gopalpura bypass", "Nirman Nagar",
    "Ramganj Bazaar", "Kukas",
]
DURATIONS = [
    "2 days", "5 din se", "one week", "10 din se", "2 weeks",
    "three days", "15 din se", "last Monday",
]
LANDMARKS = [
    "government school", "community hospital", "bus stop", "anganwadi",
    "market gate", "clinic", "metro station", "temple crossing",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["text", "category", "severity"]:
            raise ValueError(f"Unexpected columns in {path.name}: {reader.fieldnames}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path.name} is empty")
    for number, row in enumerate(rows, start=2):
        if any(not row[key] or not row[key].strip() for key in reader.fieldnames):
            raise ValueError(f"Missing value on CSV row {number}")
        for target in LABELS:
            if row[target] not in LABELS[target]:
                raise ValueError(f"Invalid {target} on CSV row {number}: {row[target]}")
    return rows


def template_signature(text: str) -> str:
    """Remove generator slot values to expose repeated wording across rows."""
    text = re.sub(r"\s+Block \d+\.|\s+गली नंबर \d+।", "", text, flags=re.I)
    for value in sorted(LOCALITIES + DURATIONS + LANDMARKS, key=len, reverse=True):
        text = re.sub(re.escape(value), "<slot>", text, flags=re.I)
    return re.sub(r"\s+", " ", text).casefold().strip()


def new_pipeline():
    return make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True),
        LogisticRegression(max_iter=2000, random_state=42),
    )


def score(model, rows, target):
    actual = [row[target] for row in rows]
    predicted = model.predict([row["text"] for row in rows])
    labels = LABELS[target]
    precision, recall, f1, _ = precision_recall_fscore_support(
        actual, predicted, labels=labels, average="macro", zero_division=0
    )
    return {
        "count": len(rows),
        "accuracy": float(accuracy_score(actual, predicted)),
        "precision_macro": float(precision),
        "recall_macro": float(recall),
        "f1_macro": float(f1),
        "labels": labels,
        "confusion_matrix": confusion_matrix(actual, predicted, labels=labels).tolist(),
        "per_class": classification_report(
            actual, predicted, labels=labels, output_dict=True, zero_division=0
        ),
    }


def train_and_score(train_rows, test_rows, target):
    model = new_pipeline()
    model.fit([row["text"] for row in train_rows], [row[target] for row in train_rows])
    return model, score(model, test_rows, target)


def main():
    rows = read_rows(DATA)
    novel_rows = read_rows(NOVEL)
    texts = [row["text"] for row in rows]
    signatures = [template_signature(text) for text in texts]
    joint_labels = [row["category"] + ":" + row["severity"] for row in rows]
    indices = list(range(len(rows)))
    train_idx, test_idx = train_test_split(
        indices, test_size=0.2, random_state=42, stratify=joint_labels
    )
    train_rows = [rows[i] for i in train_idx]
    test_rows = [rows[i] for i in test_idx]
    train_signatures = {signatures[j] for j in train_idx}

    # Select one entire wording template per category/severity pair for an
    # additional diagnostic. No template in this test set occurs in its train set.
    by_joint = defaultdict(set)
    for row, signature in zip(rows, signatures):
        by_joint[(row["category"], row["severity"])].add(signature)
    rng = random.Random(42)
    held_out = {rng.choice(sorted(options)) for options in by_joint.values()}
    group_train = [r for r, sig in zip(rows, signatures) if sig not in held_out]
    group_test = [r for r, sig in zip(rows, signatures) if sig in held_out]

    report = {
        "data": {
            "rows": len(rows),
            "columns": ["text", "category", "severity"],
            "missing_values": {key: sum(not row[key].strip() for row in rows) for key in ("text", "category", "severity")},
            "duplicate_rows": len(rows) - len({tuple(row.items()) for row in rows}),
            "duplicate_texts": len(texts) - len(set(texts)),
            "category_counts": dict(Counter(row["category"] for row in rows)),
            "severity_counts": dict(Counter(row["severity"] for row in rows)),
            "joint_counts": dict(Counter(joint_labels)),
            "word_count_range": [min(len(t.split()) for t in texts), max(len(t.split()) for t in texts)],
            "template_count_after_slot_removal": len(set(signatures)),
        },
        "splits": {
            "random_train": len(train_rows),
            "random_test": len(test_rows),
            "random_test_rows_sharing_template_with_train": sum(
                signatures[i] in train_signatures for i in test_idx
            ),
            "template_holdout_train": len(group_train),
            "template_holdout_test": len(group_test),
            "template_holdout_shared_templates": len(
                {template_signature(r["text"]) for r in group_train}
                & {template_signature(r["text"]) for r in group_test}
            ),
            "novel_wording_test": len(novel_rows),
        },
        "evaluation": {},
    }
    MODELS.mkdir(exist_ok=True)
    for target in ("category", "severity"):
        model, random_result = train_and_score(train_rows, test_rows, target)
        _, template_result = train_and_score(group_train, group_test, target)
        report["evaluation"][target] = {
            "random_holdout": random_result,
            "template_holdout": template_result,
            "novel_wording": score(model, novel_rows, target),
        }
        joblib.dump(model, MODELS / f"{target}.joblib")
    report_path = MODELS / "evaluation.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved models and evaluation to {MODELS}")
    for target in ("category", "severity"):
        scores = report["evaluation"][target]
        print(target, {name: round(result["accuracy"], 3) for name, result in scores.items()})
    print("Template diagnostic:", report["data"]["template_count_after_slot_removal"],
          "templates;", report["splits"]["random_test_rows_sharing_template_with_train"],
          "of", len(test_rows), "random test rows share one with training")


if __name__ == "__main__":
    main()
