from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_learning_coach.learning import LearningCoachService


def day_matches(actual: str | None, expected: str) -> bool:
    if actual == expected:
        return True
    if actual and "-" in actual and expected.isdigit():
        start, end = (int(item) for item in actual.split("-", 1))
        return start <= int(expected) <= end
    if expected and "-" in expected and actual and actual.isdigit():
        start, end = (int(item) for item in expected.split("-", 1))
        return start <= int(actual) <= end
    return False


def relevant(result, item: dict) -> bool:
    chunk = result.chunk
    if chunk["route"] != item["route"]:
        return False
    if item.get("phase") and str(chunk.get("phase")) != str(item["phase"]):
        return False
    if item.get("week") and str(chunk.get("week")) != str(item["week"]):
        return False
    if item.get("day") and not day_matches(chunk.get("day"), str(item["day"])):
        return False
    terms = item.get("terms_any", [])
    if terms:
        text = (chunk["heading"] + " " + chunk["body"]).casefold()
        if not any(term.casefold() in text for term in terms):
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Hibrit retrieval altın veri setini değerlendir")
    parser.add_argument("--dataset", type=Path, default=Path("tests/golden_questions.json"))
    parser.add_argument("--output", type=Path, default=Path("data/retrieval_metrics.json"))
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    service = LearningCoachService()
    ranks: list[int | None] = []
    correct_route = 0
    negatives = 0
    negative_false_positives = 0
    failures = []
    for item in dataset:
        results = service.retrieve(item["question"], top_k=6, candidate_k=30)
        if item.get("negative"):
            negatives += 1
            generic = {"hangi", "what", "which", "when", "where", "week", "haftada", "anlatılıyor", "belgeler", "öneriyor"}
            informative = {
                token.casefold().strip("?.,")
                for token in item["question"].split()
                if len(token.strip("?.,")) >= 4 and token.casefold().strip("?.,") not in generic
            }
            top_text = (
                (results[0].chunk["heading"] + " " + results[0].chunk["body"]).casefold() if results else ""
            )
            has_entity_evidence = any(token in top_text for token in informative)
            confident = bool(
                results and has_entity_evidence and results[0].bm25_score >= 2.0 and results[0].dense_score >= 0.40
            )
            negative_false_positives += int(confident)
            if confident:
                failures.append({"question": item["question"], "reason": "confident_negative_match", "top": results[0].chunk["section_path"]})
            continue
        correct_route += int(bool(results and results[0].chunk["route"] == item["route"]))
        rank = next((result.rank for result in results if relevant(result, item)), None)
        ranks.append(rank)
        if rank is None:
            failures.append(
                {
                    "question": item["question"],
                    "reason": "no_relevant_in_top6",
                    "top": [result.chunk["section_path"] for result in results[:3]],
                }
            )
    positives = len(ranks)
    metrics = {
        "questions": len(dataset),
        "positive_questions": positives,
        "negative_questions": negatives,
        "hit@1": round(sum(rank == 1 for rank in ranks) / positives, 4),
        "hit@3": round(sum(rank is not None and rank <= 3 for rank in ranks) / positives, 4),
        "hit@6": round(sum(rank is not None and rank <= 6 for rank in ranks) / positives, 4),
        "MRR": round(sum((1 / rank) if rank else 0 for rank in ranks) / positives, 4),
        "correct_document_rate": round(correct_route / positives, 4),
        "negative_false_positive_rate": round(negative_false_positives / negatives, 4) if negatives else 0.0,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics["hit@6"] >= 0.85 and metrics["correct_document_rate"] >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
