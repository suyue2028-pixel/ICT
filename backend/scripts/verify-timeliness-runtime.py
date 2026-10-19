"""Read-only runtime evidence plus a beat heartbeat observation; no model calls."""
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from sqlalchemy import select, func, text
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import DataSource, Document, KnowledgeEvent, TaskRun, VerificationProbe


def main():
    settings = get_settings()
    docker = Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
    checks, details = [], {}
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
    with get_session_factory()() as db:
        ids = {str(i) for i in db.scalars(select(Document.id).where(Document.is_demo.is_(False)))}
        baseline = json.loads((ROOT / "output/timeliness-baseline.json").read_text(encoding="utf-8-sig"))
        check("all_preupgrade_real_document_ids_retained", set(baseline["real_doc_ids"]) <= ids)
        details["baseline_real_documents"], details["current_real_documents"] = len(baseline["real_doc_ids"]), len(ids)
        check("migration_0006_current", db.scalar(text("SELECT version_num FROM alembic_version")) == "0006_source_freshness")
        heartbeat = db.get(VerificationProbe, "beat-heartbeat")
        before = heartbeat.count if heartbeat else 0
    result = subprocess.run([str(docker), "ps", "--filter", "label=com.docker.compose.project=cit-backend-development",
        "--format", "{{.Names}}|{{.Status}}"], capture_output=True, text=True, check=True)
    containers = result.stdout.strip().splitlines()
    check("exactly_one_project_beat", sum("-beat-" in c.split("|", 1)[0] for c in containers) == 1)
    check("api_postgres_redis_healthy", all(any("-" + name + "-" in c and "(healthy)" in c for c in containers)
                                            for name in ["api", "postgres", "redis"]))
    details["containers"] = containers
    deadline = time.monotonic() + 50
    after = before
    while time.monotonic() < deadline and after <= before:
        time.sleep(2)
        with get_session_factory()() as db:
            row = db.get(VerificationProbe, "beat-heartbeat")
            after = row.count if row else 0
    check("beat_redis_worker_database_heartbeat_increased", after > before)
    details["heartbeat_before"], details["heartbeat_after"] = before, after
    with get_session_factory()() as db:
        indexed_now = db.scalar(select(func.count()).select_from(Document).where(Document.is_demo.is_(False),
            Document.indexed_at.is_not(None), Document.deleted_at.is_(None)))
        details["current_versions_with_new_index_confirmation_time"] = indexed_now
        check("real_index_confirmation_time_recorded", indexed_now > 0)
        automatic = db.scalar(select(func.count()).select_from(TaskRun).where(TaskRun.kind == "collect",
            TaskRun.idempotency_key.is_(None), TaskRun.result["stale_ignored"].as_integer().is_not(None),
            TaskRun.status == "succeeded"))
        details["automatic_successful_collection_tasks_with_new_freshness_result"] = automatic
        check("beat_triggered_real_collection", automatic > 0)
    with httpx.Client(timeout=10, trust_env=False) as client:
        check("frontend_available", client.get("http://127.0.0.1:5173/").status_code == 200)
        check("frontend_backend_proxy", client.get("http://127.0.0.1:5173/api/v1/documents?time_basis=published&recent_hours=48").status_code == 200)
        values = {}
        private = Path(r"D:\cit\ICT-team\.secrets\windows-integration.env")
        for line in private.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        agent = client.get("http://127.0.0.1:9001/v1/health", headers={"Authorization": "Bearer " + values["AGENT_SERVICE_TOKEN"]})
        check("existing_agent_healthy", agent.status_code == 200)
        snapshot = client.get("http://127.0.0.1:8010/api/v1/ops/freshness",
            headers={"X-API-Key": settings.admin_api_key.get_secret_value()})
        snapshot.raise_for_status()
        details["freshness"] = snapshot.json()
    path = ROOT / "output/timeliness-runtime.json"
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "checks": checks, "details": details,
              "limits": ["PostgreSQL was not restarted during this check", "No uninterrupted 48h SLA is claimed"]}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks_passed": sum(c["passed"] for c in checks), "checks_total": len(checks),
        "report": str(path), "real_documents": details["current_real_documents"],
        "new_index_confirmations": details["current_versions_with_new_index_confirmation_time"],
        "automatic_collection_tasks": automatic, "recent": details["freshness"]["recent"]}, ensure_ascii=False))
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
