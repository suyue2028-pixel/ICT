"""Frozen synthetic corpus, real cached E5, no LLM and no production index writes."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import types
from rag_agent.config import Settings
from rag_agent.schemas import DocumentInput, Filters
from rag_agent.service import RAGService
from rag_agent.retrieval.exact import exact_lookup
from rag_agent.ingestion.normalize import identifiers

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "reports/local/windows-fullstack"

async def main():
    spec = json.loads((ROOT / "packages/rag_agent/tests/fixtures/qa_relevance_eval.json").read_text(encoding="utf-8"))
    assert spec["synthetic"]
    # Original committed retriever was unchanged before this repair. Read it
    # without changing the checkout or anyone's working files.
    original = subprocess.run(["git", "-c", "safe.directory=" + ROOT.as_posix(), "show",
        "HEAD:packages/rag_agent/src/rag_agent/retrieval/hybrid.py"], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    module = types.ModuleType("rag_agent.retrieval.baseline")
    module.__package__ = "rag_agent.retrieval"
    exec(compile(original, "baseline-retriever", "exec"), module.__dict__)
    s = RAGService(Settings(db_path=LOCAL / "qa-eval-synthetic.db", llm_mode="extractive"))
    docs = [DocumentInput(external_id=d["id"], title=d["title"], text=d["text"],
        source_name="fictional-eval", source_kind="test", source_url="https://example.org/fictional/"+d["id"],
        doc_type="blog", identifiers=[{"namespace": "CVE" if i.startswith("CVE") else "GHSA", "value": i}
            for i in identifiers(d["text"])], collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc)) for d in spec["documents"]]
    result = await s.ingest(docs)
    assert not result.failed
    snapshot = s.store.snapshot()
    titles = {d["title"]: d["id"] for d in spec["documents"]}
    retrievers = {"before": module.HybridRetriever(s.embedding), "after": s.retriever}
    rows = []
    for case in spec["cases"]:
        row = dict(case)
        for label, retriever in retrievers.items():
            ids = identifiers(case["question"])
            hits = exact_lookup(snapshot, ids, Filters(), 8)[0] if ids else await retriever.search(snapshot, case["question"], Filters(), 8)
            row[label] = list(dict.fromkeys(titles[h.title] for h in hits))[:3]
        rows.append(row)
    scores = {}
    for split in ["development", "holdout"]:
        cases = [r for r in rows if r["split"] == split]
        scores[split] = {}
        for label in retrievers:
            positives = [r for r in cases if r["gold"]]
            precision = [len(set(r[label]) & set(r["gold"])) / len(r[label]) if r[label] else 0 for r in positives]
            recall = [len(set(r[label]) & set(r["gold"])) / len(r["gold"]) for r in positives]
            scores[split][label] = {"positive_queries": len(positives),
                "mean_precision_at_3": sum(precision)/len(precision), "mean_recall_at_3": sum(recall)/len(recall),
                "top_1_hit_rate": sum(bool(r[label]) and r[label][0] in r["gold"] for r in positives)/len(positives),
                "negative_queries_correctly_empty": sum(not r[label] for r in cases if not r["gold"])}
    report = {"synthetic": True, "embedding": "real cached multilingual-e5-small", "llm_calls": 0,
        "limits": "Six fictional documents, six development and six holdout queries; not production-wide performance.",
        "scores": scores, "cases": rows}
    (LOCAL / "qa-relevance-evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"synthetic":True,"embedding":report["embedding"],"scores":scores}, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    asyncio.run(main())
