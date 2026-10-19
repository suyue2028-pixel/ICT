"""Explicit, bounded evaluation of workflow contracts; semantic review stays manual.

Default operation validates the dataset without network or model requests. Mock
mode owns a temporary SQLite index and never reads environment model settings.
Backend mode requires a user token and an explicit acknowledgement of possible
platform model/API charges. It never creates tokens, ingests fixtures or logs
credentials.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation/dynamic_questions.json"
FIXTURE = ROOT / "packages/rag_agent/tests/fixtures/documents.json"
REAL_REFERENCES = ROOT / "data/evaluation/acceptance.json"
TOOLS = {"exact_lookup", "hybrid_search", "related_evidence", "get_assertions",
         "nvd_lookup", "kev_lookup", "osv_lookup", "knowledge_lookup", "web_search"}
STATUSES = {"supported", "partial", "conflicting", "missing"}
CATEGORIES = {"greeting", "mixed_cve", "invalid_cve", "reference", "concept", "tool_audit",
              "cve_multi_requirement", "insufficient_evidence", "source_conflict", "source_failure"}
CVE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.I)


def read_json(path: Path):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("JSON input exceeds the evaluation size limit")
    return json.loads(path.read_text(encoding="utf-8"))


def load_dataset(path: Path):
    path = path.resolve()
    if not path.is_relative_to((ROOT / "evaluation").resolve()):
        raise ValueError("Dataset must remain inside the repository evaluation directory")
    data = read_json(path)
    if data.get("schema_version") != "1" or data.get("mock_fixture") != {
        "synthetic": True, "path": "packages/rag_agent/tests/fixtures/documents.json"
    } or data.get("real_reference_dataset") != "data/evaluation/acceptance.json":
        raise ValueError("Dataset source references must use the fixed, approved local files")
    # Resolve symlinks before reading fixture/reference inputs; no arbitrary paths.
    for source in (FIXTURE, REAL_REFERENCES):
        if not source.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Evaluation source escapes the repository")
    fixture, reference = read_json(FIXTURE), read_json(REAL_REFERENCES)
    if fixture.get("synthetic") is not True:
        raise ValueError("Mock intelligence must be explicitly synthetic")
    reference_ids = {item["external_id"] for row in reference["cases"] for item in row.get("references", [])}
    cases = data.get("cases")
    if not isinstance(cases, list) or not 30 <= len(cases) <= 100:
        raise ValueError("Expected 30 to 100 bounded evaluation questions")
    seen, counts = set(), Counter()
    for row in cases:
        identifier = row.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,39}", identifier) or identifier in seen:
            raise ValueError("Case IDs must be unique bounded identifiers")
        seen.add(identifier)
        category = row.get("category")
        if category not in CATEGORIES:
            raise ValueError("Unknown evaluation category")
        counts[category] += 1
        request = row.get("mock_request", {})
        question = request.get("question")
        if not isinstance(question, str) or not 0 < len(question.strip()) <= 4000:
            raise ValueError("Every case must have a bounded question")
        if "CVE-2099-" in json.dumps(request) and row.get("synthetic") is not True:
            raise ValueError("Fictional CVE cases must be marked synthetic")
        expected = row.get("expected", {})
        if not set(expected.get("required_tools", [])).issubset(TOOLS):
            raise ValueError("Expected tools must belong to the actual fixed registry")
        if not expected.get("question_types"):
            raise ValueError("Every case must declare a routing expectation")
        if row.get("semantic_review", {}).get("status") != "not_evaluated":
            raise ValueError("This implementation-authored dataset has no human semantic sign-off")
        backend = row.get("backend_request")
        if backend:
            if row.get("backend_synthetic") is not False:
                raise ValueError("Backend variants must explicitly exclude fixture intelligence")
            ids = CVE.findall(json.dumps([backend, row.get("backend_prelude", [])]))
            if not set(map(str.upper, ids)).issubset(reference_ids):
                raise ValueError("Backend CVE targets must come from the saved real reference dataset")
        if not set(row.get("real_reference_external_ids", [])).issubset(reference_ids):
            raise ValueError("Real source references are absent from the existing saved acceptance set")
    if set(counts) != CATEGORIES or any(counts[name] < 3 for name in CATEGORIES):
        raise ValueError("Each of the ten categories requires at least three cases")
    return data, {"case_count": len(cases), "category_counts": dict(sorted(counts.items())),
                  "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "fixture_synthetic": True, "semantic_gold": "not_human_reviewed"}


def ratio(values):
    reviewed = [value for value in values if isinstance(value, bool)]
    return {"passed": sum(reviewed), "evaluated": len(reviewed),
            "rate": sum(reviewed) / len(reviewed) if reviewed else None}


def token_usage(payload, summary=None):
    # No token estimation from character counts; usage is recorded only if returned.
    candidates = [payload.get("usage"), payload.get("token_usage")]
    if summary:
        candidates += summary.get("llm_usage", [])
    known = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        item = item.get("usage", item)
        result = {key: item[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                  if isinstance(item.get(key), int) and not isinstance(item[key], bool) and item[key] >= 0}
        if result:
            known.append(result)
    return known or None


def workflow_checks(payload, public=False):
    evidence = payload.get("evidence", []) if public else payload.get("sources", [])
    ids = {str(index + 1) for index in range(len(evidence))} if public else {row["evidence_id"] for row in evidence}
    requirements = payload.get("requirement_coverage", [])
    valid = isinstance(requirements, list)
    counts = Counter()
    for row in requirements if isinstance(requirements, list) else []:
        status = row.get("status")
        refs = row.get("evidence_ids", [])
        valid = valid and status in STATUSES and isinstance(refs, list) and set(refs).issubset(ids)
        if status != "missing" and not refs:
            valid = False
        counts[status] += 1
    return bool(valid), dict(counts), {row.get("requirement_id") for row in requirements if isinstance(row, dict)}


def result_row(case, payload, traces, elapsed_ms, citations_valid, public=False, summary=None):
    kind = "conversation" if payload.get("response_kind") == "conversation" else payload.get("question_type")
    tools = [row.get("tool") for row in traces]
    coverage_valid, coverage, reported = workflow_checks(payload, public)
    expected = case["expected"]
    route = kind in expected["question_types"] if kind else None
    selection = set(tools).issubset(TOOLS) and set(expected["required_tools"]).issubset(tools)
    if "conversation" in expected["question_types"] or "clarification" in expected["question_types"]:
        selection = selection and not tools
    extra = {}
    if expected.get("no_sources"):
        extra["no_source_contract"] = not (payload.get("evidence") if public else payload.get("sources"))
    if not public:
        by_id = {row["requirement_id"]: row["status"] for row in payload.get("requirement_coverage", [])}
        for label, status in (("mock_missing_requirements", "missing"), ("mock_conflicting_requirements", "conflicting")):
            if expected.get(label):
                extra[label] = all(by_id.get(identifier) == status for identifier in expected[label])
        if case.get("mock_source_fault"):
            extra["mock_fault_disclosed"] = any(row.get("status") == "failed" for row in traces) and any(
                case["mock_source_fault"] in item for item in payload.get("limitations", []))
    return {"id": case["id"], "category": case["category"], "synthetic_fixture_run": not public,
            "status": payload.get("status"), "question_type": kind,
            "routing_contract_correct": route, "tool_selection_contract_correct": selection,
            "citation_mapping_and_location_valid": citations_valid,
            "requirement_reference_contract_valid": coverage_valid,
            "requirement_reporting_complete": set(expected["required_requirement_ids"]).issubset(reported),
            "requirement_status_counts": coverage, "elapsed_ms": round(elapsed_ms, 3),
            "returned_evidence_count": len(payload.get("evidence", []) if public else payload.get("sources", [])),
            "tool_calls": len(tools), "tools": tools, "token_usage": token_usage(payload, summary),
            "cost": None, "semantic_evidence_support": "manual_not_evaluated", **extra}


async def mock_run(cases):
    sys.path.insert(0, str(ROOT / "packages/rag_agent/src"))
    from rag_agent.config import Settings
    from rag_agent.schemas import DocumentInput, QueryRequest
    from rag_agent.service import RAGService
    from rag_agent.providers.mock import MockEmbedding
    results = []
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="ict-dynamic-eval-") as directory:
        # Fresh, temporary fixture DB. Never Settings.from_env or a real index path.
        service = RAGService(Settings(db_path=Path(directory) / "synthetic.db", embedding_provider="mock",
                                     llm_mode="extractive", max_llm_calls=0, request_timeout=20), embedding=MockEmbedding())
        fixture = read_json(FIXTURE)
        ingest = await service.ingest([DocumentInput.model_validate(row) for row in fixture["documents"]])
        if ingest.failed:
            raise RuntimeError("Synthetic fixture ingestion failed")
        await service.enrich(ingest.document_ids)
        for case in cases:
            async def synthetic_source(name, args, state):
                if case.get("mock_source_fault"):
                    error = RuntimeError("explicit synthetic source fault")
                    error.code = case["mock_source_fault"]
                    raise error
                # An explicit empty test double; no claim that an official API ran.
                state.setdefault("external_checked", {})[name] = {"synthetic": True, "result": "empty"}
                return []
            started = time.monotonic()
            response = await service.query(QueryRequest.model_validate(case["mock_request"]), external_executor=synthetic_source)
            elapsed = (time.monotonic() - started) * 1000
            summary = service.get_run_summary(response.trace_id)
            valid, snapshot = True, service.store.snapshot()
            for evidence in response.sources:
                original = await service.get_evidence(evidence.evidence_id)
                valid = valid and original.model_dump() == evidence.model_dump() and snapshot.versions.get(evidence.document_id) == evidence.version
                doc = snapshot.documents[evidence.document_id]
                valid = valid and doc.source_url == evidence.url
                locator = evidence.locator
                if locator.get("kind") == "char_range":
                    valid = valid and doc.text[locator["start"]:locator["end"]] == evidence.excerpt
                elif locator.get("kind") == "json_pointer":
                    obj = doc.model_dump(mode="json")
                    try:
                        for token in locator["pointer"].lstrip("/").split("/"):
                            token = token.replace("~1", "/").replace("~0", "~")
                            obj = obj[int(token)] if isinstance(obj, list) else obj[token]
                        valid = valid and json.dumps(obj, ensure_ascii=False, sort_keys=True) == evidence.excerpt
                    except (KeyError, IndexError, ValueError, TypeError):
                        valid = False
                else:
                    valid = False
            allowed = {e.evidence_id for e in response.sources}
            valid = valid and all(set(c.evidence_ids).issubset(allowed) for c in response.claims)
            valid = valid and all(set(f.evidence_ids).issubset(allowed) for f in response.facts)
            results.append(result_row(case, response.model_dump(mode="json"), summary["tools"], elapsed, bool(valid), summary=summary))
    return results


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def backend_url(value):
    url = urlsplit(value)
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or url.scheme not in {"http", "https"} or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Backend must use HTTPS or loopback HTTP without embedded credentials")
    return value.rstrip("/")


def backend_run(cases, base, token, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def call(path, body=None):
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if body is not None:
            headers.update({"Content-Type": "application/json", "Idempotency-Key": str(uuid4())})
        request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    return 502, {"detail": "evaluation_response_too_large"}
                return response.status, json.loads(raw)
        except urllib.error.HTTPError as error:
            # Never read or print an upstream error body, cookies or credentials.
            return error.code, {}
        except (urllib.error.URLError, TimeoutError, ValueError):
            return 0, {}
    status, identity = call("/auth/me")
    if status != 200 or identity.get("authenticated") is not True:
        raise RuntimeError("Backend user authorization check failed; no evaluation query was sent")
    results, document_cache = [], {}
    for case in cases:
        if not case.get("backend_request"):
            results.append({"id": case["id"], "category": case["category"], "skipped": True,
                            "reason": case.get("backend_skip_reason", "Only supported in synthetic mock mode")})
            continue
        if case["backend_request"].get("filters"):
            results.append({"id": case["id"], "category": case["category"], "skipped": True, "reason": "Public AgentQuery does not expose direct RAG filters"})
            continue
        session_id, prelude_failed = None, False
        for question in case.get("backend_prelude", []):
            status, answer = call("/agent/query", {"query": question, "session_id": session_id, "is_demo": False})
            if status != 200:
                prelude_failed = True
                break
            session_id = answer.get("session_id")
        if prelude_failed:
            results.append({"id": case["id"], "category": case["category"], "skipped": True, "reason": "Authenticated conversation prelude did not complete"})
            continue
        started = time.monotonic()
        status, answer = call("/agent/query", {"query": case["backend_request"]["question"], "session_id": session_id, "is_demo": False})
        elapsed = (time.monotonic() - started) * 1000
        if status != 200:
            results.append({"id": case["id"], "category": case["category"], "http_status": status,
                            "request_failed": True, "elapsed_ms": round(elapsed, 3), "cost": None,
                            "semantic_evidence_support": "manual_not_evaluated"})
            continue
        sources = {(row.get("document_id"), row.get("content_hash")): row for row in answer.get("sources", [])}
        valid = True
        for evidence in answer.get("evidence", []):
            key = (evidence.get("document_id"), evidence.get("content_hash"))
            source = sources.get(key)
            valid = valid and source is not None and source.get("url") == evidence.get("source")
            try:
                UUID(str(key[0]))
            except (ValueError, TypeError):
                valid = False
                continue
            if key[0] not in document_cache:
                code, document = call("/documents/" + str(key[0]))
                document_cache[key[0]] = document if code == 200 else None
            document = document_cache[key[0]]
            if not document:
                valid = False
                continue
            revision = next((row.get("payload") for row in document.get("revisions", []) if row.get("content_hash") == key[1]), None)
            locator = re.fullmatch(r"(content|revision_json):chars:(\d+):(\d+)", evidence.get("locator", ""))
            if not revision or not locator:
                valid = False
                continue
            original = revision.get("content", "") if locator[1] == "content" else json.dumps(revision, ensure_ascii=False)
            start, end = int(locator[2]), int(locator[3])
            valid = valid and 0 <= start < end <= len(original) and original[start:end] == evidence.get("content")
        row = result_row(case, answer, answer.get("tool_trace", []), elapsed, bool(valid), public=True)
        row["http_status"] = status
        results.append(row)
    return results


def make_report(mode, metadata, results, fixed_report=None):
    completed = [row for row in results if not row.get("skipped") and not row.get("request_failed")]
    report = {"mode": mode, "dataset": metadata, "attempted": len(results),
              "evaluated": len(completed), "skipped": sum(bool(r.get("skipped")) for r in results),
              "request_failures": sum(bool(r.get("request_failed")) for r in results),
              "routing_contract_check": ratio([r.get("routing_contract_correct") for r in completed]),
              "tool_selection_contract_check": ratio([r.get("tool_selection_contract_correct") for r in completed]),
              "citation_mapping_and_location_check": ratio([r.get("citation_mapping_and_location_valid") for r in completed if r.get("returned_evidence_count", 0) > 0]),
              "empty_response_no_bogus_citation_contract": ratio([r.get("citation_mapping_and_location_valid") for r in completed if r.get("returned_evidence_count", 0) == 0]),
              "requirement_reference_contract_check": ratio([r.get("requirement_reference_contract_valid") for r in completed]),
              "requirement_reporting_completeness_check": ratio([r.get("requirement_reporting_complete") for r in completed]),
              "additional_scenario_contract_check": ratio([value for row in completed for key, value in row.items()
                                                           if key.startswith("mock_") or key == "no_source_contract"]),
              "mean_response_ms": statistics.mean(r["elapsed_ms"] for r in completed) if completed else None,
              "mean_tool_calls": statistics.mean(r["tool_calls"] for r in completed) if completed else None,
              "token_usage_available_cases": sum(r.get("token_usage") is not None for r in completed),
              "cost": None, "semantic_evidence_support_accuracy": None,
              "semantic_review": "manual_not_evaluated", "fixed_rag_baseline": None,
              "limitations": ["Structural checks are not answer accuracy or semantic entailment scores.",
                              "No model-generated factual gold or automatic confidence percentage.",
                              "Backend skips fixture-only source conflict/failure/history cases.",
                              "Provider costs are unknown; no token estimates or fabricated baseline deltas."],
              "results": results}
    if fixed_report:
        if fixed_report.resolve() != (ROOT / "reports/evaluation.json").resolve():
            raise ValueError("Fixed RAG baseline must use the existing reports/evaluation.json")
        existing = read_json(fixed_report)
        report["fixed_rag_baseline"] = {"sha256": hashlib.sha256(fixed_report.read_bytes()).hexdigest(),
                                        "available_top_level_metrics": sorted(k for k, v in existing.items() if isinstance(v, (int, float))),
                                        "comparison": "not_computed; different cases/modes need a paired run"}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate_dataset", "mock", "backend_http"), default="validate_dataset")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8010/api/v1")
    parser.add_argument("--user-token-env", default="ICT_EVAL_USER_TOKEN")
    parser.add_argument("--allow-backend-calls", action="store_true", help="Explicitly permit real authenticated calls, which may incur platform model costs")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--fixed-rag-report", type=Path)
    args = parser.parse_args(argv)
    data, metadata = load_dataset(args.dataset)
    if not 1 <= args.limit <= 100 or not 1 <= args.timeout <= 300:
        parser.error("limit must be 1..100 and timeout 1..300 seconds")
    token = ""
    if args.mode == "validate_dataset":
        report = {"mode": args.mode, "dataset": metadata, "model_calls": 0, "network_calls": 0,
                  "results": [], "semantic_evidence_support_accuracy": None, "cost": None}
    elif args.mode == "mock":
        report = make_report(args.mode, metadata, asyncio.run(mock_run(data["cases"][:args.limit])), args.fixed_rag_report)
        report.update(embedding="synthetic MockEmbedding", generation="source_excerpts_only; no remote LLM", model_calls=0, network_calls=0)
    else:
        if not args.allow_backend_calls:
            parser.error("backend_http requires --allow-backend-calls; it can invoke paid platform APIs")
        token = os.environ.get(args.user_token_env, "").strip()
        if not token or len(token) > 256 or any(char.isspace() for char in token):
            parser.error("A valid ordinary user token must be provided through the selected environment variable")
        report = make_report(args.mode, metadata, backend_run(data["cases"][:args.limit], backend_url(args.backend_url), token, args.timeout), args.fixed_rag_report)
        report["model_calls"] = None
        report["network_calls"] = "real authenticated backend calls; provider mode must be verified independently"
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    # The report contains only public checks, never raw HTTP bodies or headers.
    if token:
        rendered = rendered.replace(token, "[redacted]")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    checks = ("routing_contract_check", "tool_selection_contract_check", "citation_mapping_and_location_check",
              "empty_response_no_bogus_citation_contract", "requirement_reference_contract_check",
              "requirement_reporting_completeness_check", "additional_scenario_contract_check")
    failed = report.get("request_failures", 0) or any(report.get(key, {}).get("passed", 0) < report.get(key, {}).get("evaluated", 0) for key in checks)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        # Error bodies/credential values are not echoed by this wrapper.
        print("Evaluation could not complete: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
