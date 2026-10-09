"""Frozen draft-labelled fictional benchmark. Real cached E5, isolated index, zero paid calls."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import types

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HOME", str(ROOT / ".cache/huggingface"))
os.environ["HF_HUB_OFFLINE"] = "1"
from rag_agent.config import Settings
from rag_agent.schemas import DocumentInput, Filters, QueryRequest, Message
from rag_agent.service import RAGService
from rag_agent.retrieval.exact import exact_lookup
from rag_agent.ingestion.normalize import identifiers
from rag_agent.agent.nodes import resolve_history

BASELINE = "9daad1c2a93ac5dfcd4f699860602f9a36320611"
DATASET = ROOT / "packages/rag_agent/tests/fixtures/qa_acceptance_v1.json"
LOCAL = ROOT / "reports/local/windows-fullstack"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def baseline_module(path, package):
    raw = subprocess.run(["git", "-c", "safe.directory=" + ROOT.as_posix(), "show", BASELINE + ":" + path],
        cwd=ROOT, capture_output=True, check=True).stdout
    module = types.ModuleType(package + ".frozen_baseline")
    module.__package__ = package
    exec(compile(raw.decode("utf-8"), "frozen-baseline", "exec"), module.__dict__)
    return module, digest(raw)


def score(rows, label, k):
    positive = [r for r in rows if r["gold"]]
    negative = [r for r in rows if not r["gold"]]
    return {"positive_queries": len(positive), "negative_queries": len(negative),
        "precision_at_k": sum(len(set(r[label]) & set(r["gold"])) / k for r in positive) / len(positive) if positive else None,
        "precision_of_returned": sum(len(set(r[label]) & set(r["gold"])) / len(r[label]) if r[label] else 0 for r in positive) / len(positive) if positive else None,
        "recall_at_k": sum(len(set(r[label]) & set(r["gold"])) / len(r["gold"]) for r in positive) / len(positive) if positive else None,
        "top1_hit_rate": sum(bool(r[label]) and r[label][0] in r["gold"] for r in positive) / len(positive) if positive else None,
        "negative_correctly_empty": sum(not r[label] for r in negative)}


def check_behavior(case, response, resolved_ids, clarification):
    expected = case["expected_behavior"]
    checks = {"no_error": response.status != "error", "evidence_present_if_answered": bool(response.sources) if response.status == "ok" else True}
    if "expected_identifiers" in case:
        checks["target_resolution"] = set(resolved_ids) == set(case["expected_identifiers"])
    if expected == "clarify":
        checks["clarification"] = bool(clarification) and response.status == "insufficient_evidence" and not response.sources
    elif expected in {"no_evidence", "scope_isolation"}:
        checks["abstained"] = response.status == "insufficient_evidence" and not response.sources
    elif expected == "missing_fix":
        checks["missing_fix_disclosed"] = response.status == "partial" and any("fixed_version" in f for f in response.missing_fields)
    elif expected == "ignore_document_directive":
        checks["directive_not_executed"] = "HACKED" not in response.answer and "untrusted_document_directive" in response.warnings
    elif expected == "switch_topic":
        checks["old_identifier_absent"] = "CVE-2099-3456" not in response.answer
    return checks


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "holdout", "all"], default="all")
    args = parser.parse_args()
    spec = json.loads(DATASET.read_text(encoding="utf-8"))
    assert spec["synthetic"] and len(spec["cases"]) >= 30
    assert sum(c["split"] == "holdout" for c in spec["cases"]) >= len(spec["cases"]) / 3
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    LOCAL.mkdir(parents=True, exist_ok=True)
    baseline, baseline_hash = baseline_module("packages/rag_agent/src/rag_agent/retrieval/hybrid.py", "rag_agent.retrieval")
    settings = Settings(db_path=LOCAL / ("qa-acceptance-" + stamp + ".db"), llm_mode="extractive")
    service = RAGService(settings)
    documents = [DocumentInput(external_id=d["id"], title=d["title"], text=d["text"],
        source_name="fictional-evaluation", source_kind=d.get("source_kind", "test"), source_url="https://example.org/fictional/" + d["id"],
        doc_type="blog", collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        identifiers=[{"namespace": "CVE" if i.startswith("CVE") else "GHSA", "value": i} for i in identifiers(d["text"])]) for d in spec["documents"]]
    result = await service.ingest(documents)
    assert not result.failed
    await service.embedding.query("warmup outside timed retrieval")
    snapshot = service.store.snapshot()
    title_ids = {d["title"]: d["id"] for d in spec["documents"]}
    retrievers = {"before": baseline.HybridRetriever(service.embedding), "after": service.retriever}
    rows, behaviors = [], []
    for case in spec["cases"]:
        if args.split != "all" and case["split"] != args.split:
            continue
        filters = Filters.model_validate(case.get("filters", {"source_kinds": ["test"]}))
        row = dict(case)
        row["effective_filters"] = filters.model_dump(mode="json")
        # Preserve exact supporting quotes for a reviewer; these labels are drafts.
        row["gold_evidence"] = [{"document": d["id"], "quote": d["text"], "url": "https://example.org/fictional/" + d["id"]}
            for d in spec["documents"] if d["id"] in case["gold"]]
        if case["kind"] == "retrieval":
            for label, retriever in retrievers.items():
                start = time.perf_counter()
                ids = identifiers(case["question"])
                hits = exact_lookup(snapshot, ids, filters, 8)[0] if ids else await retriever.search(snapshot, case["question"], filters, 8)
                row[label] = list(dict.fromkeys(title_ids[h.title] for h in hits))[:3]
                row[label + "_ms"] = round((time.perf_counter() - start) * 1000, 2)
            rows.append(row)
        else:
            history = [Message.model_validate(m) for m in case.get("history", [])]
            _, resolved, _, clarification = resolve_history(case["question"], history, {}, snapshot.documents)
            start = time.perf_counter()
            response = await service.query(QueryRequest(question=case["question"], history=history, filters=filters))
            row.update(result=response.model_dump(mode="json"), elapsed_ms=round((time.perf_counter()-start)*1000, 2),
                resolved_identifiers=resolved, checks=check_behavior(case, response, resolved, clarification))
            row["checks"]["evidence_integrity"] = all(e.evidence_id in snapshot.evidence and
                snapshot.evidence[e.evidence_id].model_dump() == e.model_dump() for e in response.sources)
            row["review"] = {"answer_relevance": None, "citation_support": None, "reviewer": None}
            behaviors.append(row)
    scores = {split: {label: score([r for r in rows if r["split"] == split], label, 3) for label in retrievers}
              for split in {r["split"] for r in rows}}
    code_files = ["retrieval/hybrid.py", "agent/nodes.py", "providers/llm.py", "schemas.py"]
    report = {"time": datetime.now(timezone.utc).isoformat(), "synthetic": True,
        "annotation_state": spec["annotation_state"], "baseline_commit": BASELINE, "baseline_code_sha256": baseline_hash,
        "dataset_sha256": digest(DATASET.read_bytes()), "code_sha256": {p: digest((ROOT / "packages/rag_agent/src/rag_agent" / p).read_bytes()) for p in code_files},
        "embedding_model": settings.embedding_model, "embedding_revision": settings.embedding_revision,
        "parameters": {"k_documents": 3, "chunk_tokens": settings.chunk_tokens, "overlap_tokens": settings.overlap_tokens,
            "dense_threshold": 0.78, "rrf_offset": 60, "default_scope": "fictional test source_kind"},
        "llm_calls": 0, "index_path": str(settings.db_path), "scores": scores, "cases": rows, "behavior_cases": behaviors,
        "behavior_checks_passed": sum(all(r["checks"].values()) for r in behaviors), "behavior_checks_total": len(behaviors),
        "precision_definition": "P@3 uses fixed denominator 3; precision_of_returned uses returned document count; all doc IDs deduplicated",
        "answer_quality_and_semantic_citation_support": "pending independent human review; extractive controls are not real generation quality scores",
        "limits": "36 draft-labelled fictional questions, 24 development + 12 held out; not production-wide accuracy. No threshold tuning on held-out questions."}
    path = LOCAL / ("qa-acceptance-" + stamp + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "scores": scores, "behavior_checks_passed": report["behavior_checks_passed"],
        "behavior_checks_total": len(behaviors), "llm_calls": 0, "annotation_state": spec["annotation_state"]}, ensure_ascii=False, indent=2))
    return 0 if all(all(r["checks"].values()) for r in behaviors) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
