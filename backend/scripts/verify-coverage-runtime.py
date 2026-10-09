"""Read-only runtime, inventory-cycle, source-code and persisted observation checks."""
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
import httpx
from sqlalchemy import select, func

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import DataSource, Document, TaskRun, RuntimeSample
from backend.collectors.extended import ExtendedConfig
from backend.collectors.live import SourceConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-inventory", type=int, default=0)
    parser.add_argument("--compose-project", default="cit-backend-development")
    args = parser.parse_args()
    if not 0 <= args.wait_inventory <= 1200:
        parser.error("--wait-inventory must be between 0 and 1200 seconds")
    baseline = json.loads((ROOT / "output/coverage-strengthening-baseline.json").read_text(encoding="utf-8"))
    started = datetime.fromisoformat(baseline["measured_at"])
    deadline = time.monotonic() + args.wait_inventory
    while True:
        with get_session_factory()() as db:
            sources = list(db.scalars(select(DataSource).where(DataSource.adapter.in_(("osv", "nvd")))))
            cycles = {}
            for source in sources:
                observations = db.scalars(select(TaskRun.result).where(TaskRun.source_id == source.id,
                    TaskRun.created_at >= started, TaskRun.status == "succeeded"))
                expected = (SourceConfig.validate_for("osv", source.config).packages if source.adapter == "osv"
                            else ExtendedConfig.model_validate(source.config).keywords)
                observed = set()
                for result in observations:
                    coverage = result.get("coverage", {})
                    value = coverage.get("package") if source.adapter == "osv" else coverage.get("recent_window", {}).get("keyword")
                    if value:
                        observed.add(value)
                cycles[source.adapter] = {"configured": expected, "successfully_checked": sorted(observed),
                    "missing": sorted(set(expected) - observed)}
        if all(not value["missing"] for value in cycles.values()) or time.monotonic() >= deadline:
            break
        print(json.dumps({"inventory_cycle_observation": {key: {"checked": len(value["successfully_checked"]),
            "configured": len(value["configured"])} for key, value in cycles.items()}}, ensure_ascii=False), flush=True)
        time.sleep(min(30, max(0, deadline - time.monotonic())))
    settings = get_settings()
    headers = {"X-API-Key": settings.admin_api_key.get_secret_value()}
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", headers=headers, timeout=30, trust_env=False) as client:
        response = client.get("/api/v1/ops/coverage"); response.raise_for_status()
        snapshot = response.json()
        checks = {"api_ready": client.get("/health/ready").status_code == 200,
            "ops_requires_admin": client.get("/api/v1/ops/coverage", headers={"X-API-Key": "invalid"}).status_code == 401,
            "coverage_openapi": "/api/v1/ops/coverage" in client.get("/openapi.json").json()["paths"],
            "osv_expanded_inventory_succeeded": "osv" in cycles and not cycles["osv"]["missing"],
            "nvd_expanded_inventory_succeeded": "nvd" in cycles and not cycles["nvd"]["missing"]}
    docker = shutil.which("docker") or str(Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe")
    hashes = {}
    relatives = ["backend/collectors/base.py", "backend/collectors/live.py", "backend/collectors/extended.py",
        "backend/collectors/discovery.py", "backend/collectors/registry.py", "backend/services/coverage.py",
        "backend/services/freshness.py", "backend/services/observation.py", "backend/services/pipeline.py",
        "backend/tasks/jobs.py", "backend/api/v1/operations.py", "backend/api/v1/sources.py", "backend/schemas/contracts.py"]
    for relative in relatives:
        result = subprocess.run([docker, "exec", args.compose_project + "-api-1", "python", "-c",
            "from pathlib import Path; import hashlib; print(hashlib.sha256(Path(" + repr("/app/" + relative) + ").read_bytes()).hexdigest())"],
            capture_output=True, text=True, check=True)
        hashes[relative] = result.stdout.strip() == hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    checks["running_code_matches_source"] = all(hashes.values())
    beat = subprocess.run([docker, "ps", "--filter", "label=com.docker.compose.project=" + args.compose_project,
        "--filter", "label=com.docker.compose.service=beat", "--format", "{{.ID}}"], capture_output=True, text=True, check=True)
    checks["single_beat"] = len(beat.stdout.split()) == 1
    with get_session_factory()() as db:
        latest = db.scalar(select(RuntimeSample).order_by(RuntimeSample.measured_at.desc()).limit(1))
        checks["coverage_automatically_persisted"] = bool(latest and "source_coverage" in latest.detail)
        count_preserved = []
        for before in baseline["sources"]:
            current = db.scalar(select(func.count()).select_from(Document).where(Document.source_id == before["id"],
                Document.is_demo.is_(False), Document.deleted_at.is_(None)))
            count_preserved.append(current >= before["documents"])
        checks["baseline_source_document_counts_preserved"] = all(count_preserved)
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        expected_head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
        checks["production_schema_matches_source"] = db.connection().exec_driver_sql("select version_num from alembic_version").scalar() == expected_head
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "checks": checks, "passed": all(checks.values()),
        "inventory_cycles": cycles, "source_hashes": hashes, "snapshot": snapshot,
        "model_calls": 0, "production_data_mutated_by_verifier": False,
        "limitations": ["Baseline preservation check compares source counts, not byte-for-byte database equivalence",
            "A successful inventory cycle does not prove external exhaustive recall or continuous timeliness"]}
    folder = ROOT / "output/coverage"; folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("deployment-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"report": str(path), "checks": checks, "inventory_cycles": cycles,
        "recent_delivery": snapshot["delivery"]["recent_publication_to_first_collection"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
