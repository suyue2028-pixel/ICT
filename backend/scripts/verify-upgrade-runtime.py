"""Local runtime evidence, image hashes and secret non-disclosure; no model requests."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import httpx
from sqlalchemy import select, func
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import DataSource, Document, RuntimeObservation, RuntimeSample


def main():
    s = get_settings()
    docker = Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
    checks = {}
    with httpx.Client(timeout=15, trust_env=False) as client:
        checks["api_ready"] = client.get(f"http://127.0.0.1:{s.api_port}/health/ready").status_code == 200
        checks["frontend_ready"] = client.get("http://127.0.0.1:5173/qa").status_code == 200
        headers = {"X-API-Key": s.admin_api_key.get_secret_value()}
        checks["agent_ready"] = client.get(f"http://127.0.0.1:{s.api_port}/api/v1/agent/health", headers=headers).status_code == 200
        freshness = client.get(f"http://127.0.0.1:{s.api_port}/api/v1/ops/freshness", headers=headers).json()
        checks["observation_requires_admin"] = client.post(f"http://127.0.0.1:{s.api_port}/api/v1/ops/observations", json={}).status_code == 401
        checks["strict_observation_body_deployed"] = client.post(f"http://127.0.0.1:{s.api_port}/api/v1/ops/observations", json={"hours": True}, headers=headers).status_code == 422
    raw = subprocess.run([str(docker), "ps", "--filter", "label=com.docker.compose.project=cit-backend-development", "--format", "{{.Names}}"],
        capture_output=True, check=True).stdout.decode().splitlines()
    checks["exactly_one_beat"] = sum(name.endswith("-beat-1") for name in raw) == 1 and sum("-beat-" in name for name in raw) == 1
    hashes = {}
    for file in ["backend/models/entities.py", "backend/api/v1/operations.py", "backend/services/observation.py",
                 "backend/services/enrichment.py", "backend/services/agent_gateway.py", "backend/tasks/jobs.py", "backend/tasks/celery_app.py"]:
        code = "import hashlib; print(hashlib.sha256(open('/app/" + file + "','rb').read()).hexdigest())"
        result = subprocess.run([str(docker), "exec", "cit-backend-development-api-1", "python", "-c", code], capture_output=True)
        hashes[file] = {"source": hashlib.sha256((ROOT / file).read_bytes()).hexdigest(),
            "running": result.stdout.decode().strip() if result.returncode == 0 else None}
    checks["container_code_matches_source"] = all(r["source"] == r["running"] for r in hashes.values())
    # Known configured secrets are compared in memory, never printed.
    values = [getattr(s, name).get_secret_value() for name in ["admin_api_key", "deepseek_api_key", "embedding_api_key",
        "postgres_password", "redis_password", "agent_tool_token", "agent_write_token", "agent_service_token"] if getattr(s, name, None)]
    hits = []
    for service in ["api", "worker", "beat"]:
        log = subprocess.run([str(docker), "logs", "--tail", "300", "cit-backend-development-" + service + "-1"], capture_output=True)
        if any(value.encode() in log.stdout + log.stderr for value in values if value):
            hits.append("container:" + service)
    frontend = Path(r"D:\cit\ICT-team\reports\local\integration\frontend")
    for file in frontend.rglob("*"):
        if not file.is_file() or any(p in {"node_modules", ".git", "dist"} for p in file.parts) or file.stat().st_size > 2_000_000:
            continue
        if any(value.encode() in file.read_bytes() for value in values if value):
            hits.append(str(file))
    checks["no_known_secrets_in_checked_logs_or_frontend_files"] = not hits
    with get_session_factory()() as db:
        sources = list(db.scalars(select(DataSource).where(DataSource.adapter != "offline")))
        counts = {"real_documents": db.scalar(select(func.count()).select_from(Document).where(Document.is_demo.is_(False))),
            "adapter_count": len({r.adapter for r in sources}), "enabled_adapter_count": len({r.adapter for r in sources if r.enabled}),
            "source_category_count": len({r.category for r in sources}),
            "evaluation_sources_in_production": db.scalar(select(func.count()).select_from(DataSource).where(
                DataSource.name.like("fictional-performance-%") | DataSource.name.like("fictional-evaluation%")))}
        run = db.scalar(select(RuntimeObservation).order_by(RuntimeObservation.started_at.desc()).limit(1))
        samples = db.scalar(select(func.count()).select_from(RuntimeSample).where(RuntimeSample.observation_id == run.id)) if run else 0
        checks["automatic_observation_samples_persisted"] = samples >= 2
        checks["evaluation_data_not_in_production"] = counts["evaluation_sources_in_production"] == 0
        checks["twelve_adapters_seven_categories"] = counts["adapter_count"] == 12 and counts["source_category_count"] == 7
    result = {"time": datetime.now(timezone.utc).isoformat(), "checks": checks, "passed": all(checks.values()),
        "counts": counts, "sample_count": samples, "container_source_hashes": hashes, "secret_match_locations": hits,
        "source_statuses": [{k:r[k] for k in ("adapter", "enabled", "status", "last_success_at", "last_error_code", "interval_seconds")} for r in freshness["sources"]],
        "recent_48_hours": freshness["recent"], "unindexed_current_documents": freshness["unindexed_current_documents"],
        "limits": "Instant checks and limited known-secret scan; not continuous uptime, universal secret detection, or full quality approval"}
    (ROOT / "output/upgrade-runtime.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": checks, "counts": counts, "sample_count": samples}, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        raise SystemExit("Runtime verification failed: " + type(error).__name__ + "; inspect local services") from None
