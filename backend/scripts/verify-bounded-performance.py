"""Bounded local reads and rollback-only synthetic pipeline. Never calls a paid model."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time
from uuid import uuid4
import httpx
from sqlalchemy.orm import Session
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db.session import get_engine
from backend.core.config import get_settings
from backend.collectors.base import CollectionBatch
from backend.models import DataSource, TaskRun
from backend.schemas.contracts import RawRecord
from backend.services.pipeline import ingest_batch


def summary(values):
    return {"count": len(values), "min_ms": round(min(values), 2), "median_ms": round(statistics.median(values), 2),
        "max_ms": round(max(values), 2), "p95_ms": None, "note": "Small bounded sample; P95 not reported"}


def main():
    settings = get_settings()
    def request(index):
        started = time.perf_counter()
        with httpx.Client(timeout=15, trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{settings.api_port}/api/v1/documents",
                                  params={"is_demo": False, "page_size": 20, "page": index % 3 + 1})
        return response.status_code, (time.perf_counter() - started) * 1000
    with ThreadPoolExecutor(max_workers=2) as pool:
        reads = list(pool.map(request, range(20)))
    samples = []
    with get_engine().connect() as connection, connection.begin() as transaction:
        with Session(connection, join_transaction_mode="create_savepoint") as db:
            source = DataSource(name="fictional-performance-" + str(uuid4()), adapter="offline", category="offline", config={})
            db.add(source)
            db.flush()
            records = [RawRecord(external_id="fictional-" + str(i), title="Fictional AI security performance sample",
                content="Fictional LLM prompt injection sample for rollback-only performance verification.",
                source_url="https://example.invalid/performance/" + str(i), document_type="article", is_demo=True) for i in range(20)]
            outcomes = []
            for _ in range(3):
                task = TaskRun(source_id=source.id, status="running", attempts=1)
                db.add(task)
                db.flush()
                started = time.perf_counter()
                result = ingest_batch(db, source, CollectionBatch(records, {}), task)
                task.status = "succeeded"
                db.flush()
                samples.append((time.perf_counter() - started) * 1000)
                outcomes.append(result)
            transaction.rollback()
    result = {"time": datetime.now(timezone.utc).isoformat(), "passed": all(status == 200 for status, _ in reads),
        "read_conditions": {"requests": 20, "concurrency": 2, "page_size": 20, "existing_collectors_active": True},
        "document_list": summary([duration for _, duration in reads]), "http_statuses": [status for status, _ in reads],
        "pipeline": summary(samples), "pipeline_batch_documents": 20, "pipeline_outcomes": outcomes,
        "pipeline_scope": "fictional rollback-only transaction; not Celery end-to-end throughput",
        "llm_calls": 0, "limits": "Local bounded samples are not capacity, saturation or production SLA measurements"}
    path = ROOT / "output/bounded-performance.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "document_list": result["document_list"], "pipeline": result["pipeline"],
        "pipeline_outcomes": outcomes, "llm_calls": 0}, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
