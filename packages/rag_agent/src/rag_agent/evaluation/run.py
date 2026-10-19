"""Frozen-reference retrieval evaluation; human semantic judgments stay pending."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import time
from ..config import Settings
from ..schemas import QueryRequest
from ..service import RAGService


def normalize(text):
    return " ".join(text.casefold().split())


def percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower) if lower != upper else ordered[lower]


def pointer(root, value):
    for part in value.lstrip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        root = root[int(part)] if isinstance(root, list) else root[part]
    return root


def citation_locates(evidence, snapshot):
    doc = snapshot.documents.get(evidence.document_id)
    if not doc or evidence.version != snapshot.versions[evidence.document_id] or evidence.url != doc.source_url:
        return False
    loc = evidence.locator
    if loc.get("kind") == "char_range":
        return doc.text[loc["start"]:loc["end"]] == evidence.excerpt
    if loc.get("kind") == "json_pointer":
        try:
            return pointer(doc.model_dump(mode="json"), loc["pointer"]) == json.loads(evidence.excerpt)
        except (KeyError, IndexError, ValueError, TypeError):
            return False
    return False


def gold_evidence(case, snapshot):
    targets, unavailable = set(), []
    for reference in case.get("references", []):
        doc_ids = {i for i, doc in snapshot.documents.items() if doc.external_id == reference["external_id"]}
        matches = {e.evidence_id for e in snapshot.evidence.values()
                   if e.document_id in doc_ids and e.locator["kind"] == "char_range"
                   and normalize(reference["span"]) in normalize(e.excerpt)}
        if not matches:
            unavailable.append(reference)
        targets.update(matches)
    return targets, unavailable


async def evaluate(service, cases_path, output):
    raw = cases_path.read_bytes()
    specification = json.loads(raw)
    snapshot = service.store.snapshot()
    if specification["split"] != "acceptance":
        raise ValueError("this run requires a frozen acceptance file")
    results, latencies, recalls, citations = [], [], [], []
    correct_fields = answered_fields = expected_fields = covered_fields = 0
    all_started = time.perf_counter()
    for number, case in enumerate(specification["cases"]):
        targets, unavailable = gold_evidence(case, snapshot)
        response = await service.query(QueryRequest.model_validate(case["request"]))
        summary = service.get_run_summary(response.trace_id)
        retrieved = summary["evidence_ids"]
        recall = len(targets.intersection(retrieved[:5])) / len(targets) if targets and not unavailable else None
        if recall is not None:
            recalls.append(recall)
        located = [citation_locates(e, snapshot) for e in response.sources]
        citations.extend(located)
        latencies.append(response.metrics.elapsed_ms)
        golds = case.get("gold_facts", [])
        expected_fields += len(golds)
        for gold in golds:
            covered_fields += any(snapshot.documents[a.document_id].external_id == gold["external_id"] and a.field == gold["field"]
                                  and isinstance(a.value, dict) and all(a.value.get(k) == v for k, v in gold["value"].items()) for a in response.facts)
        scope = {(g["external_id"], g["field"]) for g in golds}
        for assertion in response.facts:
            key = (snapshot.documents[assertion.document_id].external_id, assertion.field)
            if key in scope:
                answered_fields += 1
                correct_fields += any((g["external_id"], g["field"]) == key and isinstance(assertion.value, dict)
                                      and all(assertion.value.get(k) == v for k, v in g["value"].items()) for g in golds)
        checks = {"status": response.status in case["expected_status"], "references_available": not unavailable,
                  "citations_locate": all(located), "budgets": response.metrics.llm_calls <= 3 and response.metrics.tool_calls <= 8 and response.metrics.retries <= 1}
        if case.get("missing_contains"):
            checks["missing_field"] = any(case["missing_contains"] in m for m in response.missing_fields)
        expected_docs = case.get("expected_documents", [])
        returned_docs = {snapshot.documents[e.document_id].external_id for e in response.sources}
        if expected_docs:
            checks["document_coverage"] = set(expected_docs).issubset(returned_docs)
        if case.get("no_evidence"):
            checks["no_evidence"] = not response.sources
        results.append({"id": case["id"], "category": case["category"], "passed_automated_checks": all(checks.values()),
                        "checks": checks, "recall_at_5": recall, "gold_evidence_ids": sorted(targets),
                        "unavailable_reference_spans": unavailable, "retrieved_evidence_ids": retrieved,
                        "response": response.model_dump(mode="json"), "trace": summary,
                        "human_support_judgment": None, "human_reasoning_judgment": None})
    # A second request measures a warm path, with identical reference specification.
    warm = await service.query(QueryRequest.model_validate(specification["cases"][0]["request"]))
    metrics = {
        "sample_count": len(results), "automated_passed": sum(r["passed_automated_checks"] for r in results),
        "retrieval_recall_at_5": statistics.mean(recalls) if recalls else None,
        "recall_scored_questions": len(recalls), "citation_locatable_rate": sum(citations) / len(citations) if citations else None,
        "citation_sample_count": len(citations),
        "field_accuracy_within_declared_cvss_reference_slots": correct_fields / answered_fields if answered_fields else None,
        "field_accuracy_answered_count": answered_fields, "field_coverage": covered_fields / expected_fields if expected_fields else None,
        "field_expected_count": expected_fields, "human_citation_support_rate": None,
        "cross_document_reasoning_correctness": None, "human_evaluation_status": "pending_human_review",
        "cold_first_query_ms": latencies[0] if latencies else None, "warm_same_query_ms": warm.metrics.elapsed_ms,
        "end_to_end_p50_ms": percentile(latencies, .5), "end_to_end_p95_ms": percentile(latencies, .95),
        "all_cases_elapsed_ms": (time.perf_counter() - all_started) * 1000,
        "llm_calls": sum(r["response"]["metrics"]["llm_calls"] for r in results),
        "tool_calls": sum(r["response"]["metrics"]["tool_calls"] for r in results),
    }
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "split": "acceptance", "cases_sha256": hashlib.sha256(raw).hexdigest(),
              "reference_author": specification["reference_author"], "human_review_status": specification["human_review_status"],
              "configuration": {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor(),
                                "embedding": service.embedding.signature(), "embedding_mode": service.embedding.mode,
                                "generation_mode": service.settings.llm_mode, "device": service.settings.embedding_device,
                                "documents": len(snapshot.documents), "chunks": len(snapshot.chunks), "generation": snapshot.generation,
                                "dependencies": {p: importlib.metadata.version(p) for p in ["langgraph", "sentence-transformers", "pydantic", "numpy", "torch"]}},
              "metrics": metrics, "cases": results}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    review = [{"case_id": r["id"], "claims": r["response"]["claims"], "sources": r["response"]["sources"],
               "citation_support": None, "cross_document_score": None, "reviewer": None, "notes": ""} for r in results]
    output.with_name("human_review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2))
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("data/evaluation/acceptance.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    args = parser.parse_args()
    asyncio.run(evaluate(RAGService(Settings.from_env()), args.cases, args.output))


if __name__ == "__main__":
    main()
