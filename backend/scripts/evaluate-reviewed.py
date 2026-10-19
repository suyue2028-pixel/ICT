"""Score independently reviewed field detection and QA; no fabricated labels/default passes."""
import argparse
import json
from pathlib import Path


def report(payload):
    cases = payload.get("cases", [])
    reviewed = [c for c in cases if c.get("review", {}).get("reviewer") and c["review"].get("reviewed_at")]
    triage = [c for c in reviewed if type(c["review"].get("ai_relevant")) is bool]
    correct = sum(c["review"]["ai_relevant"] == c["predicted_ai_relevant"] for c in triage)
    tp = fp = fn = 0
    field_cases = [c for c in reviewed if isinstance(c["review"].get("expected_fields"), dict)]
    for case in field_cases:
        expected = {(k, json.dumps(v, sort_keys=True, ensure_ascii=False)) for k, v in case["review"]["expected_fields"].items()}
        predicted = {(p["field"], json.dumps(p["value"], sort_keys=True, ensure_ascii=False))
                     for p in case["predicted_fields"] if p["state"] == "known"}
        tp += len(expected & predicted)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
    qa = [c for c in reviewed if type(c["review"].get("answer_correct")) is bool
          and c.get("question") and isinstance(c.get("response"), dict) and c["response"].get("answer")]
    citations = [c for c in qa if type(c["review"].get("citation_correct")) is bool]
    return {"total_cases": len(cases), "reviewed_cases": len(reviewed),
        "ai_triage": {"sample_size": len(triage), "accuracy": correct / len(triage) if triage else None},
        "enrichment_exact_field_values": {"sample_size": len(field_cases), "tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "definition": "exact normalized field-value match; document corpus coverage is a separate metric"},
        "qa": {"sample_size": len(qa), "accuracy": sum(c["review"]["answer_correct"] for c in qa) / len(qa) if qa else None,
            "citation_sample_size": len(citations),
            "citation_accuracy": sum(c["review"]["citation_correct"] for c in citations) / len(citations) if citations else None},
        "limitations": "No score from unreviewed cases; small or non-random samples do not prove competition thresholds"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(report(json.loads(args.path.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2))
