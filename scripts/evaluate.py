"""Evaluate synthetic examples; the default makes no network requests.

Usage: python scripts/evaluate.py [--configured-provider] [--output report.json]
Use --configured-provider only to deliberately evaluate your configured model.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.intelligence import Intelligence  # noqa: E402
from app.knowledge import KnowledgeBase  # noqa: E402


def evaluate(settings: Settings) -> dict:
    cases = json.loads((ROOT / "data" / "eval_cases.json").read_text(encoding="utf-8"))
    intelligence, knowledge = Intelligence(settings), KnowledgeBase()
    rows = []
    confusion = Counter()
    for case in cases:
        result = intelligence.classify(case["message"])
        actual = result.classification.intent.value
        correct = actual == case["expected_intent"]
        hits = [article.id for article in knowledge.search(case["message"])]
        row = {
            **case,
            "actual_intent": actual,
            "correct": correct,
            "source": result.source,
            "fallback_reason": result.fallback_reason,
        }
        if "expected_article" in case:
            row["retrieval_hit_at_3"] = case["expected_article"] in hits
            row["retrieved_article_ids"] = hits
        confusion[(case["expected_intent"], actual)] += 1
        rows.append(row)
    retrieval_rows = [row for row in rows if "retrieval_hit_at_3" in row]
    correct_count = sum(row["correct"] for row in rows)
    return {
        "provider": settings.llm_provider,
        "cases": len(rows),
        "correct": correct_count,
        "accuracy": correct_count / len(rows),
        "retrieval_cases": len(retrieval_rows),
        "retrieval_hits_at_3": sum(row["retrieval_hit_at_3"] for row in retrieval_rows),
        "fallback_count": sum(row["source"] == "mock_fallback" for row in rows),
        "confusion": [
            {"expected": expected, "actual": actual, "count": count}
            for (expected, actual), count in sorted(confusion.items())
        ],
        "limitations": "Small hand-authored synthetic English set, not held out; not production accuracy. Rules and examples were developed together. Hard paraphrases intentionally expose rule limitations. Retrieval hit@3 does not prove answer quality. Model confidence is not calibrated. No private-data access is exercised by this classification evaluation; integration tests cover authorization.",
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configured-provider", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env() if args.configured_provider else Settings(llm_provider="mock")
    report = evaluate(settings)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
