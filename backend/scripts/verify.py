"""真实验证新项目的 API、数据库、Redis、worker、beat；不打印任何凭据。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx
from redis import Redis
from sqlalchemy import func, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.config import get_settings
from backend.db.session import get_engine, get_session_factory
from backend.models import Document, Evidence, VerificationProbe, Vulnerability


def docker_command():
    executable = shutil.which("docker")
    if not executable:
        candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe",
                      Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")]
        executable = next((str(path) for path in candidates if path.is_file()), None)
    if executable is None:
        raise RuntimeError("Docker CLI unavailable")
    return executable


def compose(*args):
    result = subprocess.run([docker_command(), "compose", *args], cwd=ROOT,
                            text=True, encoding="utf-8", capture_output=True, timeout=180)
    if result.returncode:
        # config 或日志可能包含凭据，不将原始输出写入验收报告。
        raise RuntimeError(f"Compose operation failed: {args[0]}")
    return result.stdout


def wait_task(client, task):
    for _ in range(60):
        response = client.get(f"/api/v1/tasks/{task['id']}")
        response.raise_for_status()
        task = response.json()
        if task["status"] == "succeeded":
            return task
        if task["status"] == "failed":
            raise RuntimeError(f"Task failed: {task['error_code']}")
        time.sleep(1)
    raise RuntimeError("Worker task did not complete within 60 seconds")


def counts(demo_only=False):
    with get_session_factory()() as db:
        documents = select(func.count()).select_from(Document)
        vulnerabilities = select(func.count()).select_from(Vulnerability)
        evidence = select(func.count()).select_from(Evidence)
        if demo_only:
            documents = documents.where(Document.is_demo.is_(True))
            vulnerabilities = vulnerabilities.where(Vulnerability.is_demo.is_(True))
            evidence = evidence.join(Document).where(Document.is_demo.is_(True))
        return {"documents": db.scalar(documents), "vulnerabilities": db.scalar(vulnerabilities),
                "evidence": db.scalar(evidence)}


def persistence_probe(create=False):
    with get_session_factory()() as db, db.begin():
        row = db.get(VerificationProbe, "database-persistence")
        if row is None and create:
            row = VerificationProbe(key="database-persistence", count=271828)
            db.add(row)
        return row.count if row else None


def heartbeat_count():
    with get_session_factory()() as db:
        return db.get(VerificationProbe, "beat-heartbeat").count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart-check", action="store_true", help="仅重启本项目服务以验证持久化与启停")
    args = parser.parse_args()
    settings = get_settings()
    report = {"project": "后端开发", "scope": "backend-offline-verification", "checks": {}}
    checks = report["checks"]
    checks["compose_parse"] = compose("config", "--quiet") == ""
    process_output = compose("ps", "--all", "--format", "json").strip()
    processes = json.loads(process_output) if process_output.startswith("[") else [
        json.loads(line) for line in process_output.splitlines() if line.strip()
    ]
    checks["linux_containers"] = all(
        subprocess.run([docker_command(), "inspect", "--format", "{{.Platform}}", item["ID"]],
                       capture_output=True, text=True, check=True).stdout.strip() == "linux"
        for item in processes
    )
    runtime = [item for item in processes if item["Service"] in {"api", "postgres", "redis", "worker", "beat"}]
    checks["five_services_running"] = len(runtime) == 5 and all(item["State"] == "running" for item in runtime)
    checks["single_beat"] = len([item for item in runtime if item["Service"] == "beat"]) == 1
    images = [subprocess.run([docker_command(), "inspect", "--format", "{{.Image}}", item["ID"]],
                             text=True, capture_output=True, check=True).stdout.strip()
              for item in runtime if item["Service"] in {"api", "worker", "beat"}]
    checks["shared_backend_image"] = len(images) == 3 and len(set(images)) == 1
    with get_engine().connect() as connection:
        checks["postgres_query"] = connection.scalar(text("SELECT 1")) == 1
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        config = Config()
        config.set_main_option("script_location", str(ROOT / "migrations"))
        checks["migration"] = connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(config).get_current_head()
    with Redis.from_url(settings.redis_url(), socket_timeout=3) as redis:
        checks["redis_ping"] = bool(redis.ping())
    headers = {"X-API-Key": settings.admin_api_key.get_secret_value()}
    base_url = f"http://127.0.0.1:{settings.api_port}"
    with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
        checks["api_docs"] = client.get("/docs").status_code == 200
        checks["api_ready"] = client.get("/health/ready").status_code == 200
        checks["write_auth"] = client.post("/api/v1/demo/import").status_code == 401
        response = client.post("/api/v1/demo/import", headers=headers)
        checks["async_accepted"] = response.status_code == 202
        response.raise_for_status()
        first = wait_task(client, response.json())
        before = counts(demo_only=True)
        response = client.post("/api/v1/demo/import", headers=headers)
        response.raise_for_status()
        repeated = wait_task(client, response.json())
        checks["worker_completed"] = first["status"] == repeated["status"] == "succeeded"
        steps = client.get(f"/api/v1/tasks/{repeated['id']}").json()["steps"]
        checks["step_duration_recorded"] = bool(steps) and all(
            isinstance(step["detail"].get("duration_ms"), (int, float)) for step in steps)
        checks["idempotent_ingestion"] = counts(demo_only=True) == before and repeated["result"]["unchanged"] == 3
        demo = client.get("/api/v1/intelligence", params={"is_demo": "true"}).json()
        live = client.get("/api/v1/intelligence").json()
        checks["demo_separated"] = demo["total"] >= 1 and all(not item["is_demo"] for item in live["items"])
        detail = client.get(f"/api/v1/intelligence/{demo['items'][0]['id']}").json()
        checks["document_association_and_evidence"] = len(detail["documents"]) >= 2 and len(detail["evidence"]) >= 3
        source = next(item for item in client.get("/api/v1/sources").json() if item["adapter"] == "osv")
        checks["live_adapter_available"] = source["adapter"] == "osv" and isinstance(source["config"], dict)
        if settings.embedding_configured:
            # Offline verification must not trigger a billable embedding call.
            checks["semantic_contract"] = "/api/v1/qa/semantic-search" in client.get("/openapi.json").json()["paths"]
        else:
            checks["semantic_contract"] = client.post("/api/v1/qa/semantic-search", headers=headers,
                json={"question": "offline verification"}).status_code == 503
        checks["keyword_search_cites_source"] = bool(client.post("/api/v1/qa/search", json={
            "question": "输入", "document_ids": []}, params={"is_demo": "true"}).json().get("citations"))
        baseline = heartbeat_count()
        for _ in range(45):
            if heartbeat_count() > baseline:
                checks["beat_triggered_worker"] = True
                break
            time.sleep(1)
        else:
            checks["beat_triggered_worker"] = False
        if args.restart_check:
            # Enabled real collectors may legitimately add data. Check the isolated
            # demo rows and a dedicated persistent sentinel, not equality of all counts.
            before_restart = counts(demo_only=True)
            saved_probe = persistence_probe(create=True)
            compose("restart", "postgres")
            for _ in range(30):
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            checks["database_restart_persistence"] = counts(demo_only=True) == before_restart and persistence_probe() == saved_probe
            compose("stop")
            compose("start", "--wait", "--wait-timeout", "120")
            checks["stop_start"] = client.get("/health/ready").status_code == 200 and counts(demo_only=True) == before_restart and persistence_probe() == saved_probe
    report["counts"] = counts()
    report["api_docs"] = f"{base_url}/docs"
    report["passed"] = all(checks.values())
    (ROOT / "output").mkdir(exist_ok=True)
    (ROOT / "output/verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
