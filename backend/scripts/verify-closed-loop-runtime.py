"""Read-only deployment fingerprint, schema, health and actual beat heartbeat check."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx
from redis import Redis
from sqlalchemy import select, func, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import Document, DocumentRevision, VerificationProbe, AssociationEvidence


def main():
    docker = shutil.which("docker") or str(Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe")
    settings = get_settings()
    checks = {}
    def command(*args):
        return subprocess.run([docker, *args], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    files = sorted([*ROOT.joinpath("backend").rglob("*.py"), *ROOT.joinpath("migrations").rglob("*.py")])
    local = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    code = "import json,hashlib,pathlib; print(json.dumps({p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for root in ['backend','migrations'] for p in pathlib.Path(root).rglob('*.py')}))"
    images = []
    for service in ("api", "worker", "beat"):
        name = "cit-backend-development-" + service + "-1"
        running = json.loads(command("exec", name, "python", "-c", code))
        checks[service + "_all_python_hashes_match"] = running == local
        images.append(command("inspect", "--format", "{{.Image}}", name))
    checks["shared_image"] = len(set(images)) == 1
    beats = command("ps", "--filter", "label=com.docker.compose.project=cit-backend-development",
        "--filter", "label=com.docker.compose.service=beat", "--format", "{{.ID}}")
    checks["one_production_beat"] = len(beats.split()) == 1
    parsed = subprocess.run([docker, "compose", "config", "--quiet"], cwd=ROOT, capture_output=True)
    checks["compose_parse"] = parsed.returncode == 0
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", timeout=15, trust_env=False) as client:
        checks["api_ready"] = client.get("/health/ready").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        checks["traceability_routes_deployed"] = all(p in paths for p in (
            "/api/v1/documents/{document_id}/versions/{content_hash}", "/api/v1/documents/{document_id}/associations",
            "/api/v1/agent/invocations/{invocation_id}/evidence"))
        checks["agent_health"] = client.get("/api/v1/agent/health",
            headers={"X-API-Key": settings.admin_api_key.get_secret_value()}).status_code == 200
    with Redis.from_url(settings.redis_url(), socket_timeout=3) as redis:
        checks["production_redis_ping"] = bool(redis.ping())
    with get_session_factory()() as db:
        before = db.get(VerificationProbe, "beat-heartbeat").count
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["migration_0009"] = version == "0009_association_evidence"
        counts = {"documents": db.scalar(select(func.count()).select_from(Document)),
            "document_revisions": db.scalar(select(func.count()).select_from(DocumentRevision)),
            "association_evidence": db.scalar(select(func.count()).select_from(AssociationEvidence))}
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        time.sleep(1)
        with get_session_factory()() as db:
            after = db.get(VerificationProbe, "beat-heartbeat").count
        if after > before:
            break
    checks["beat_redis_worker_database_heartbeat"] = after > before
    baseline = json.loads((ROOT / "output/traceability/baseline.json").read_text(encoding="utf-8"))
    checks["baseline_counts_preserved"] = all(counts[t] >= baseline["tables"][t] for t in ("documents", "document_revisions"))
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()),
        "checks": checks, "python_files_per_service": len(local), "source_hashes": local,
        "counts": counts, "image_id": images[0], "model_calls": 0,
        "limits": "Existing source count comparison is not byte-equivalence; isolated snapshot restore separately compares table fingerprints."}
    path = ROOT / "output/traceability/runtime-deployment.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "python_files_per_service": len(local), "report": str(path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
