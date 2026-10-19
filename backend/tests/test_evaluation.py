"""Known confusion-matrix counts; unreviewed or absent QA must never score."""
import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate-reviewed.py"
spec = importlib.util.spec_from_file_location("reviewed_evaluation", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_score_denominators_and_no_unreviewed_passes():
    case = {"review": {"reviewer": "test-only-independent-review", "reviewed_at": "2026-10-02",
        "ai_relevant": False, "expected_fields": {"cwe": {"ids": ["CWE-502"]}, "analysis": {"references": ["test-missing"]}},
        "answer_correct": True}, "predicted_ai_relevant": True,
        "predicted_fields": [{"field": "cwe", "state": "known", "value": {"ids": ["CWE-502"]}},
                             {"field": "cvss", "state": "known", "value": {"score": 1}}]}
    report = module.report({"cases": [case, {"review": {"ai_relevant": True}}]})
    fields = report["enrichment_exact_field_values"]
    assert (fields["tp"], fields["fp"], fields["fn"]) == (1, 1, 1)
    assert fields["precision"] == fields["recall"] == .5
    assert report["ai_triage"] == {"sample_size": 1, "accuracy": 0}
    assert report["qa"]["sample_size"] == 0 and report["qa"]["accuracy"] is None
