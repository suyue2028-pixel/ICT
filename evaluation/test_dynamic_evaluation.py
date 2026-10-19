"""Dataset/report unit tests. No backend, model or network calls."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evaluate_dynamic", ROOT / "scripts/evaluate_dynamic.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class DynamicEvaluationTests(unittest.TestCase):
    def test_thirty_questions_ten_categories_and_marked_fixtures(self):
        data, metadata = evaluation.load_dataset(evaluation.DEFAULT_DATASET)
        self.assertGreaterEqual(metadata["case_count"], 30)
        self.assertEqual(set(metadata["category_counts"]), evaluation.CATEGORIES)
        self.assertTrue(all(count >= 3 for count in metadata["category_counts"].values()))
        self.assertTrue(data["mock_fixture"]["synthetic"])
        self.assertTrue(all(row["semantic_review"]["status"] == "not_evaluated" for row in data["cases"]))

    def test_source_paths_cannot_read_private_or_external_files(self):
        for name in (ROOT / ".secrets/llm-api-key", Path("/private/tmp/untrusted.json")):
            with self.assertRaises(ValueError):
                evaluation.load_dataset(name)

    def test_synthetic_cases_cannot_lose_their_explicit_marker(self):
        real_reader = evaluation.read_json
        bad = deepcopy(real_reader(evaluation.DEFAULT_DATASET))
        row = next(row for row in bad["cases"] if "CVE-2099-" in str(row["mock_request"]))
        row["synthetic"] = False
        with patch.object(evaluation, "read_json", side_effect=lambda path: bad if path == evaluation.DEFAULT_DATASET else real_reader(path)):
            with self.assertRaises(ValueError):
                evaluation.load_dataset(evaluation.DEFAULT_DATASET)

    def test_supported_requirement_needs_a_valid_returned_citation(self):
        payload = {"evidence": [{"document_id": "public-doc"}], "requirement_coverage": [
            {"requirement_id": "overview", "status": "supported", "evidence_ids": ["2"]}
        ]}
        self.assertFalse(evaluation.workflow_checks(payload, public=True)[0])
        payload["requirement_coverage"][0]["evidence_ids"] = ["1"]
        self.assertTrue(evaluation.workflow_checks(payload, public=True)[0])

    def test_absent_token_counts_and_semantic_accuracy_remain_unknown(self):
        self.assertIsNone(evaluation.token_usage({}))
        self.assertEqual(evaluation.token_usage({"usage": {"total_tokens": 12}}), [{"total_tokens": 12}])
        report = evaluation.make_report("mock", {}, [])
        self.assertIsNone(report["cost"])
        self.assertIsNone(report["semantic_evidence_support_accuracy"])
        self.assertEqual(report["semantic_review"], "manual_not_evaluated")

    def test_backend_target_requires_secure_transport_and_no_embedded_credentials(self):
        self.assertEqual(evaluation.backend_url("http://127.0.0.1:8010/api/v1/"), "http://127.0.0.1:8010/api/v1")
        for url in ("http://remote.example/api/v1", "https://user:password@example.org", "https://example.org?secret=value"):
            with self.assertRaises(ValueError):
                evaluation.backend_url(url)

    def test_failed_source_scenario_is_counted_and_empty_citations_are_separate(self):
        row = {"id": "synthetic-fault", "elapsed_ms": 1, "tool_calls": 1, "returned_evidence_count": 0,
               "citation_mapping_and_location_valid": True, "mock_fault_disclosed": False}
        report = evaluation.make_report("mock", {}, [row])
        self.assertEqual(report["additional_scenario_contract_check"], {"passed": 0, "evaluated": 1, "rate": 0.0})
        self.assertEqual(report["citation_mapping_and_location_check"]["evaluated"], 0)
        self.assertEqual(report["empty_response_no_bogus_citation_contract"]["evaluated"], 1)


if __name__ == "__main__":
    unittest.main()
