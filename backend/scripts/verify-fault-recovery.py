"""Real container faults in a NEW isolated project; never stop production or delete volumes."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="cit-backend-development:local")
    args = parser.parse_args()
    docker = shutil.which("docker") or str(Path(os.environ.get("LOCALAPPDATA", "")) /
        "Programs/DockerDesktop/resources/bin/docker.exe")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    project = "verify-fault-" + stamp.lower() + "-" + uuid4().hex[:6]
    assert project.startswith("verify-fault-")
    folder = ROOT / "output/reliability" / project
    folder.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "VERIFY_PASSWORD": secrets.token_urlsafe(32),
        "VERIFY_ADMIN": secrets.token_urlsafe(32), "VERIFY_IMAGE": args.image}
    shared = {"POSTGRES_HOST": "postgres", "POSTGRES_PORT": "5432", "POSTGRES_DB": "verify_fault",
        "POSTGRES_USER": "verify_fault", "POSTGRES_PASSWORD": "${VERIFY_PASSWORD:?}",
        "REDIS_HOST": "redis", "REDIS_PORT": "6379", "REDIS_PASSWORD": "${VERIFY_PASSWORD:?}",
        "ADMIN_API_KEY": "${VERIFY_ADMIN:?}", "INDEX_PROVIDER": "external"}
    api = {"image": "${VERIFY_IMAGE:?}", "environment": shared,
        "command": ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"],
        "ports": ["127.0.0.1::8000"]}
    compose = {"services": {
        "postgres": {"image": "postgres:16-bookworm@sha256:efedf3595f1d6f415c08568ba171029bf54052e754cc9f030e3f2412b21f3d67",
            "environment": {"POSTGRES_DB": "verify_fault", "POSTGRES_USER": "verify_fault", "POSTGRES_PASSWORD": "${VERIFY_PASSWORD:?}"},
            "volumes": ["verify_pg:/var/lib/postgresql/data"],
            "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U verify_fault -d verify_fault"], "interval": "2s", "timeout": "2s", "retries": 30}},
        "redis": {"image": "redis:7.4-bookworm@sha256:c6eabf748fc7a61dbb5a705c78bcf3d6377b1127a97d0ce965c11c44ba46896f",
            "command": ["redis-server", "--appendonly", "yes", "--appendfsync", "everysec", "--requirepass", "${VERIFY_PASSWORD:?}"],
            "volumes": ["verify_redis:/data"]},
        "migrate": {"image": "${VERIFY_IMAGE:?}", "environment": shared, "command": ["alembic", "upgrade", "head"]},
        "api": api,
        "worker": {"image": "${VERIFY_IMAGE:?}", "environment": shared,
            "command": ["celery", "-A", "backend.tasks.celery_app:celery_app", "worker", "--loglevel=warning", "--concurrency=1"]}},
        "volumes": {"verify_pg": {}, "verify_redis": {}}}
    path = folder / "compose.json"
    path.write_text(json.dumps(compose, indent=2), encoding="utf-8")
    command = [docker, "compose", "-p", project, "-f", str(path)]
    checks, report = [], {"project": project, "started_at": datetime.now(timezone.utc).isoformat(),
        "production_services_modified": False, "volumes_retained": True, "external_model_calls": 0,
        "limits": "One isolated Redis outage, queued-message/AOF restart, worker pause and normal PostgreSQL restart. Not a host disaster or a measured production RTO/RPO."}
    def check(name, passed, **details):
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            raise RuntimeError("Verification check failed: " + name)
    with (folder / "private.log").open("w", encoding="utf-8") as log:
        def run(*args, capture=False):
            value = subprocess.run([*command, *args], env=env, cwd=ROOT,
                stdout=subprocess.PIPE if capture else log, stderr=log, timeout=180, check=True)
            return value.stdout.decode("utf-8").strip() if capture else None
        def db_query(sql):
            return run("exec", "-T", "postgres", "psql", "-U", "verify_fault", "-d", "verify_fault", "-Atc", sql, capture=True)
        def maintenance():
            run("exec", "-T", "api", "python", "-c",
                "from backend.tasks.jobs import maintenance; maintenance.run()")
        def wait_task(client, task_id):
            deadline = time.monotonic() + 70
            while time.monotonic() < deadline:
                value = client.get("/api/v1/tasks/" + task_id).json()
                if value["status"] in {"succeeded", "failed"}:
                    return value
                time.sleep(1)
            raise RuntimeError("Verification task timeout")
        try:
            run("config", "--quiet")
            run("up", "-d", "--pull", "never", "--wait", "postgres", "redis")
            run("run", "--rm", "--no-deps", "migrate")
            run("up", "-d", "--pull", "never", "--no-deps", "api", "worker")
            address = run("port", "api", "8000", capture=True)
            with httpx.Client(base_url="http://" + address, trust_env=False, timeout=15) as client:
                deadline = time.monotonic() + 45
                while True:
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Verification API not ready")
                    time.sleep(1)
                admin = {"X-API-Key": env["VERIFY_ADMIN"]}
                source = client.post("/api/v1/sources", headers=admin,
                    json={"name": "[FICTIONAL] fault recovery only", "adapter": "offline"})
                source.raise_for_status()
                source_id = source.json()["id"]
                run("stop", "--timeout", "10", "redis")
                task = client.post(f"/api/v1/sources/{source_id}/collect", headers={**admin, "Idempotency-Key": project})
                check("api_accepts_durable_task_during_broker_outage", task.status_code == 202)
                task_id = task.json()["id"]
                failure_started = time.monotonic()
                maintenance()
                check("broker_failure_publish_is_bounded", time.monotonic()-failure_started <= 20,
                    failure_publish_seconds=round(time.monotonic()-failure_started, 3), limit_seconds=20)
                check("outbox_retained_and_cursor_not_advanced", db_query(
                    "SELECT count(*) FROM outbox o JOIN task_runs t ON t.id=o.task_id JOIN data_sources s ON s.id=t.source_id "
                    "WHERE t.id='" + task_id + "' AND t.status='queued' AND o.sent_at IS NULL AND o.attempts>=1 AND s.cursor::text='{}'") == "1")
                started = time.monotonic()
                run("start", "redis")
                time.sleep(6)  # Includes outbox retry delay; no second beat is created.
                maintenance()
                first = wait_task(client, task_id)
                check("outbox_replayed_and_worker_completed", first["status"] == "succeeded",
                      recovery_seconds=round(time.monotonic()-started, 3))
                check("three_fictional_documents", db_query("SELECT count(*) FROM documents WHERE is_demo") == "3")
                repeat = client.post(f"/api/v1/sources/{source_id}/collect", headers={**admin, "Idempotency-Key": project})
                check("same_request_same_task", repeat.json()["id"] == task_id)
                # Queue while worker is stopped, persist Redis AOF, restart then consume.
                run("stop", "--timeout", "15", "worker")
                second = client.post(f"/api/v1/sources/{source_id}/collect", headers=admin)
                second.raise_for_status()
                second_id = second.json()["id"]
                maintenance()
                time.sleep(2)
                check("message_published_before_broker_restart", db_query(
                    "SELECT count(*) FROM outbox WHERE task_id='" + second_id + "' AND sent_at IS NOT NULL") == "1")
                run("restart", "redis")
                run("start", "worker")
                recovered = wait_task(client, second_id)
                check("queued_message_survived_redis_restart", recovered["status"] == "succeeded")
                check("duplicate_import_no_duplicate_versions", db_query("SELECT count(*) FROM document_revisions") == "3")
                before = db_query("SELECT string_agg(content_hash,',' ORDER BY id) FROM documents")
                run("restart", "postgres")
                deadline = time.monotonic() + 45
                while True:
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Database restart recovery timed out")
                    time.sleep(1)
                check("postgres_restart_preserved_document_hashes", before == db_query(
                    "SELECT string_agg(content_hash,',' ORDER BY id) FROM documents"))
                check("association_proof_persisted", db_query("SELECT count(*) FROM association_evidence") == "2")
                check("no_extra_scheduler", "beat" not in run("ps", "--services", capture=True).splitlines())
                report["migration"] = db_query("SELECT version_num FROM alembic_version")
        except Exception as error:
            report["error_type"] = type(error).__name__
        finally:
            try:
                run("stop", "--timeout", "15")
            except Exception:
                report["stop_incomplete"] = True
    report.update({"checks": checks, "passed": bool(checks) and all(c["passed"] for c in checks)
        and "error_type" not in report and not report.get("stop_incomplete"),
        "finished_at": datetime.now(timezone.utc).isoformat()})
    (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "report": str(folder / "result.json"),
        "error_type": report.get("error_type")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
