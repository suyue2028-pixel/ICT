"""Read-only real database/API/worker/version-citation verification; no model calls."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import UUID

import httpx
from sqlalchemy import select, text, func
from sqlalchemy.orm import load_only

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import Document, DocumentRevision, Evidence, TaskRun, Outbox
from backend.services.eight_enrichment import DIMENSIONS, METHOD, PROCESSOR_VERSION, processing_stats
from backend.services.pipeline import payload_hash
from backend.services.traceability import locate_quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-project", required=True)
    args = parser.parse_args()
    checks, errors, samples = {}, [], {}
    checked_quotes = 0
    with get_session_factory()() as db, db.begin():
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        stats = processing_stats(db)
        docs = list(db.scalars(select(Document).options(load_only(Document.id, Document.content_hash,
            Document.document_type, Document.raw_payload, Document.source_url, Document.is_demo))
            .where(Document.is_demo.is_(False), Document.deleted_at.is_(None))))
        documents = {doc.id: doc for doc in docs}
        payloads = {(doc.id, doc.content_hash): doc.raw_payload for doc in docs}
        rows = db.scalars(select(Evidence).join(Document, Evidence.document_id == Document.id).where(
            Document.is_demo.is_(False), Document.deleted_at.is_(None), Evidence.content_hash == Document.content_hash,
            Evidence.method == METHOD, Evidence.value["processor_version"].as_string() == PROCESSOR_VERSION)).all()
        dimensions = defaultdict(set)
        for row in rows:
            dimensions[row.document_id].add(row.field)
            doc = documents[row.document_id]
            # Prefer a vulnerability with an affected-product result for UI/API samples.
            if doc.document_type not in samples or row.field == "affected" and row.state == "known":
                samples[doc.document_type] = str(doc.id)
            proof = row.value.get("evidence", [])
            if row.state in {"known", "pending_review"} and not proof:
                errors.append({"document_id": str(doc.id), "field": row.field, "code": "facts_without_evidence"})
            if row.field == "papers" and any(item.get("relation") == "topic_candidate_needs_review"
                    for item in row.value.get("value", [])) and row.state != "pending_review":
                errors.append({"document_id": str(doc.id), "field": row.field, "code": "paper_candidate_claimed_confirmed"})
            for quote in proof:
                try:
                    identity = UUID(quote["document_id"]), quote["content_hash"]
                    if identity not in payloads:
                        revision = db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == identity[0],
                            DocumentRevision.content_hash == identity[1]))
                        referenced = db.get(Document, identity[0])
                        if revision is None or referenced is None or referenced.is_demo:
                            raise ValueError("missing_or_demo_version")
                        payloads[identity] = revision.payload
                    payload = payloads[identity]
                    assert quote["source_url"] == payload["source_url"]
                    assert payload_hash(payload) == identity[1]
                    assert locate_quote(payload, quote["excerpt"], quote["locator"])["locator_verified"]
                    checked_quotes += 1
                except Exception:
                    errors.append({"document_id": str(doc.id), "field": row.field, "code": "version_quote_invalid"})
        checks["all_real_current_documents_have_eight_current_processor_results"] = (
            bool(docs) and all(dimensions[doc.id] == set(DIMENSIONS) for doc in docs))
        checks["source_payload_hashes_unchanged"] = all(payload_hash(doc.raw_payload) == doc.content_hash for doc in docs)
        checks["all_fact_quotes_and_paper_states_verified"] = not errors and checked_quotes > 0
        task_groups = [list(row) for row in db.execute(select(TaskRun.status, TaskRun.error_code, func.count()).where(
            TaskRun.kind == "enrich", TaskRun.result["processor_version"].as_string() == PROCESSOR_VERSION)
            .group_by(TaskRun.status, TaskRun.error_code))]
        dispatched = db.scalar(select(func.count()).select_from(Outbox).join(TaskRun, Outbox.task_id == TaskRun.id).where(
            TaskRun.kind == "enrich", TaskRun.status == "succeeded", Outbox.sent_at.is_not(None),
            TaskRun.result["processor_version"].as_string() == PROCESSOR_VERSION))
        checks["real_outbox_and_worker_completed_jobs"] = dispatched > 0
        migration = db.scalar(text("SELECT version_num FROM alembic_version"))
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        checks["migration_applied"] = migration == ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    settings = get_settings()
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", trust_env=False, timeout=30,
            headers={"X-API-Key": settings.admin_api_key.get_secret_value()}) as client:
        checks["api_ready"] = client.get("/health/ready").status_code == 200
        live = client.get("/api/v1/ops/enrichment"); live.raise_for_status()
        checks["live_api_reports_full_processing"] = live.json()["remaining_documents"] == 0
        sample_results = []
        for kind, identity in samples.items():
            response = client.get(f"/api/v1/documents/{identity}/enrichment"); response.raise_for_status()
            summary = response.json()["summary"]
            sample_results.append({"document_id": identity, "type": kind, "states": {k: v["state"] for k, v in summary.items()}})
        checks["sample_apis_show_eight_resolved_states"] = bool(sample_results) and all(len(s["states"]) == 8 and
            not set(s["states"].values()) & {"unprocessed", "queued", "processing", "failed"} for s in sample_results)
        freshness = client.get("/api/v1/ops/freshness").json()
        checks["freshness_target_still_four_hours"] = freshness["schedule_policy"]["target_seconds"] == 14400
        quality = client.get("/api/v1/ops/enrichment-quality").json()
        checks["quality_scan_complete"] = quality["scan_complete"] and quality["total_documents"] == quality["scanned_documents"]
    docker = shutil.which("docker") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe")
    def command(*values):
        result = subprocess.run([docker, *values], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("container_check_failed")
        return result.stdout.strip()
    paths = ["backend/services/eight_enrichment.py", "backend/services/cvss_base.py", "backend/tasks/jobs.py",
             "backend/services/jobs.py", "backend/api/v1/documents.py", "backend/api/v1/operations.py"]
    expected = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    code = "import json,hashlib; from pathlib import Path; print(json.dumps({p:hashlib.sha256(Path('/app',p).read_bytes()).hexdigest() for p in " + repr(paths) + "}))"
    checks["api_worker_beat_run_current_code"] = all(json.loads(command("exec", args.compose_project + "-" + service + "-1",
        "python", "-c", code)) == expected for service in ("api", "worker", "beat"))
    checks["single_business_beat"] = len(command("ps", "--filter", "label=com.docker.compose.project=" + args.compose_project,
        "--filter", "label=com.docker.compose.service=beat", "--format", "{{.ID}}").splitlines()) == 1
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "stats": stats, "checked_quote_occurrences": checked_quotes, "errors": errors,
        "task_groups": task_groups, "dispatched_successful_jobs": dispatched, "samples": sample_results,
        "model_calls": 0, "verifier_business_writes": 0, "human_accuracy": None,
        "limits": "Eight processing results do not mean eight known facts. Quote integrity is not independent semantic accuracy or worldwide recall."}
    folder = ROOT / "output/enrichment-eight"; folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("runtime-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"report": str(path), **report}, ensure_ascii=False, indent=2, default=str))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        raise SystemExit("Eight-dimension verification failed: " + type(error).__name__ + "; no credentials printed") from None
