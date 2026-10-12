"""Read-only checks of the deployed four-hour policy; no model calls or task creation."""
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
from sqlalchemy import select, func, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import Document, TaskRun, VerificationProbe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--compose-project", default=os.environ.get("COMPOSE_PROJECT_NAME", "ict-backend"))
    args = parser.parse_args()
    if not 0 <= args.wait_seconds <= 60:
        parser.error("--wait-seconds must be between 0 and 60")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    deadline = time.monotonic() + args.wait_seconds
    while True:
        with get_session_factory()() as db:
            heartbeat = db.scalar(select(VerificationProbe.count).where(VerificationProbe.key == "beat-heartbeat"))
            probe = None
            if baseline:
                probe = db.scalar(select(TaskRun).where(TaskRun.source_id == UUID(baseline["source_id"]),
                    TaskRun.created_at >= datetime.fromisoformat(baseline["started_at"]))
                    .order_by(TaskRun.created_at.desc()).limit(1))
                observed = bool(heartbeat > baseline["heartbeat"] and probe and probe.status in {"succeeded", "failed"})
            else:
                observed = True
            probe_summary = {"id": str(probe.id), "status": probe.status, "error_code": probe.error_code,
                "attempts": probe.attempts} if probe else None
        if observed or time.monotonic() >= deadline:
            break
        time.sleep(min(2, max(0, deadline - time.monotonic())))

    settings = get_settings()
    headers = {"X-API-Key": settings.admin_api_key.get_secret_value()}
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", headers=headers,
                      trust_env=False, timeout=20) as client:
        checks = {"api_ready": client.get("/health/ready").status_code == 200,
            "agent_health": client.get("/api/v1/agent/health").status_code == 200,
            "freshness_requires_admin": client.get("/api/v1/ops/freshness", headers={"X-API-Key": "invalid"}).status_code == 401,
            "four_hour_document_filter": client.get("/api/v1/documents?recent_hours=4&time_basis=published").status_code == 200}
        response = client.get("/api/v1/ops/freshness")
        response.raise_for_status()
        freshness = response.json()
    policy = freshness["schedule_policy"]
    checks["four_hour_policy"] = policy["target_seconds"] == policy["max_failure_backoff_seconds"] == 14400
    checks["continuous_delivery_not_falsely_verified"] = policy["delivery_target_verified"] is False
    sources = [{key: row.get(key) for key in ("adapter", "enabled", "interval_seconds",
        "configured_interval_seconds", "four_hour_schedule_applicable", "schedule_exception",
        "status", "last_success_at", "next_run_at", "last_error_code")} for row in freshness["sources"]]
    enabled = [row for row in sources if row["enabled"]]
    checks["enabled_schedule_limits"] = bool(enabled) and all(
        row["interval_seconds"] <= (86400 if row["adapter"] == "arxiv" else 14400) for row in enabled)
    checks["daily_cache_disclosed"] = all(not row["four_hour_schedule_applicable"]
        and row["schedule_exception"] == "daily_api_cache" for row in enabled if row["adapter"] == "arxiv")
    checks["actual_latency_target"] = freshness["publish_to_first_collection_seconds"]["recent_publications"]["target_seconds"] == 14400
    docker = shutil.which("docker") or str(Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe")
    def command(*arguments):
        return subprocess.run([docker, *arguments], capture_output=True, text=True, check=True).stdout.strip()
    beats = command("ps", "--filter", "label=com.docker.compose.project=" + args.compose_project,
        "--filter", "label=com.docker.compose.service=beat", "--format", "{{.ID}}")
    checks["single_project_beat"] = len(beats.split()) == 1
    relatives = ["backend/services/freshness.py", "backend/api/v1/sources.py"]
    expected = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in relatives}
    code = "import hashlib,json; from pathlib import Path; print(json.dumps({p:hashlib.sha256(Path('/app',p).read_bytes()).hexdigest() for p in " + repr(relatives) + "}))"
    hashes = {service: json.loads(command("exec", args.compose_project + "-" + service + "-1", "python", "-c", code)) == expected
              for service in ("api", "worker", "beat")}
    checks["running_source_matches"] = all(hashes.values())
    with get_session_factory()() as db:
        documents = db.scalar(select(func.count()).select_from(Document))
        migration = db.execute(text("select version_num from alembic_version")).scalar()
        duplicates = db.execute(select(TaskRun.source_id, func.count()).where(
            TaskRun.status.in_(("queued", "running"))).group_by(TaskRun.source_id).having(func.count() > 1)).all()
    checks["no_overlapping_source_tasks"] = not duplicates
    if baseline:
        checks["beat_heartbeat_advanced"] = heartbeat > baseline["heartbeat"]
        checks["scheduled_probe_created"] = probe_summary is not None
        checks["scheduled_real_collection_succeeded"] = bool(probe_summary and probe_summary["status"] == "succeeded")
        checks["document_count_preserved"] = documents >= baseline["previous_deploy_document_count"]
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()),
        "checks": checks, "sources": sources, "runtime_source_hashes_match": hashes,
        "scheduled_probe": probe_summary, "heartbeat": heartbeat, "documents": documents,
        "migration": migration, "actual_recent_publication_latency": freshness["publish_to_first_collection_seconds"]["recent_publications"],
        "actual_update_latency": freshness["source_update_to_changed_version_seconds"],
        "model_calls": 0, "verifier_mutates_business_data": False,
        "limitations": ["arXiv is a daily-cache exception", "Immediate checks do not establish a continuous four-hour delivery SLA",
            "Count preservation is not a byte-for-byte database comparison"]}
    out = ROOT / "output/four-hour"
    out.mkdir(parents=True, exist_ok=True)
    path = out / ("runtime-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"report": str(path), "checks": checks, "passed": report["passed"],
        "scheduled_probe": probe_summary, "actual_recent_publication_latency": report["actual_recent_publication_latency"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        raise SystemExit("Runtime verification failed: " + type(error).__name__ + "; no secrets printed") from None
