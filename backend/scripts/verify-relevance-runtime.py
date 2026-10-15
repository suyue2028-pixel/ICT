"""Read-only verification of a real applied reclassification and Agent index proofs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import UUID
import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import Document, KnowledgeEvent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--applied", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--compose-project", required=True, help="Actual Compose project name; never guess another deployment")
    args = parser.parse_args()
    if not 0 <= args.wait_seconds <= 60:
        parser.error("--wait-seconds must be 0..60")
    applied = json.loads(args.applied.read_text(encoding="utf-8"))
    changes = applied["changes"]
    deadline = time.monotonic() + args.wait_seconds
    while True:
        results = []
        with get_session_factory()() as db:
            for row in changes:
                doc = db.get(Document, UUID(row["document_id"]))
                event = db.get(KnowledgeEvent, UUID(row["event_id"]))
                results.append({"document_id": row["document_id"], "event_id": row["event_id"],
                    "operation": row["operation"], "expected_ai_relevant": row["ai_relevant"],
                    "ai_relevant": doc.ai_relevant, "source_hash_retained": doc.content_hash == row["content_hash"],
                    "source_not_deleted": doc.deleted_at is None, "triage_method": doc.relevance.get("method"),
                    "status": event.status, "error_code": event.detail.get("error_code")})
        completed = all(row["status"] == ("invalidated" if row["operation"] == "delete" else "indexed") for row in results)
        if completed or time.monotonic() >= deadline:
            break
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    checks = {"scope_update_events_completed": bool(results) and completed,
        "all_source_versions_retained": all(row["source_hash_retained"] and row["source_not_deleted"] for row in results),
        "classification_matches_plan": all(row["ai_relevant"] == row["expected_ai_relevant"] and row["triage_method"] == applied["method"] for row in results)}
    settings = get_settings()
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", trust_env=False, timeout=20,
        headers={"X-API-Key": settings.admin_api_key.get_secret_value()}) as client:
        checks["api_ready"] = client.get("/health/ready").status_code == 200
        checks["agent_healthy"] = client.get("/api/v1/agent/health").status_code == 200
        checks["original_documents_still_accessible"] = all(client.get("/api/v1/documents/" + row["document_id"]).status_code == 200 for row in results)
        checks["freshness_still_four_hours"] = client.get("/api/v1/ops/freshness").json()["schedule_policy"]["target_seconds"] == 14400
        quality = client.get("/api/v1/ops/enrichment-quality").json()
        checks["quality_scan_still_complete"] = quality["scan_complete"] and quality["total_documents"] == quality["scanned_documents"]
    docker = shutil.which("docker") or str(Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe")
    def command(*values):
        result = subprocess.run([docker, *values], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("runtime_proof_check_failed")
        return result.stdout.strip()
    if completed:
        event_ids = [row["event_id"] for row in results]
        code = ("import json; from uuid import UUID; from backend.core.config import get_settings; "
            "from backend.db.session import get_session_factory; from backend.models import KnowledgeEvent,Document; "
            "from backend.services.agent_gateway import verify_external_index; "
            "db=get_session_factory()(); s=get_settings(); "
            "events=[db.get(KnowledgeEvent,UUID(i)) for i in " + repr(event_ids) + "]; "
            "[verify_external_index(s,e,db.get(Document,e.document_id).is_demo,e.detail.get('index_id')) for e in events]; "
            "print(json.dumps({'verified':len(events)})); db.close()")
        proof = json.loads(command("exec", args.compose_project + "-api-1", "python", "-c", code))
        checks["agent_real_index_proofs_verified"] = proof["verified"] == len(results)
    else:
        checks["agent_real_index_proofs_verified"] = False
    paths = ["backend/services/relevance.py", "backend/services/reclassification.py", "backend/services/pipeline.py",
        "backend/api/v1/ai.py", "backend/api/v1/indexing.py", "backend/services/jobs.py",
        "backend/tasks/jobs.py", "backend/services/agent_gateway.py"]
    expected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
    code = "import json,hashlib; from pathlib import Path; print(json.dumps({p:hashlib.sha256(Path('/app',p).read_bytes()).hexdigest() for p in " + repr(paths) + "}))"
    checks["deployed_sources_match"] = all(json.loads(command("exec", args.compose_project + "-" + service + "-1", "python", "-c", code)) == expected
                                           for service in ("api", "worker", "beat"))
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "changes": results, "model_calls": 0, "verifier_business_writes": 0,
        "quality_scope_documents": quality["total_documents"], "human_accuracy": None}
    folder = ROOT / "output/relevance"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("runtime-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), **report}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        raise SystemExit("Runtime verification failed: " + type(error).__name__ + "; no secrets printed") from None
