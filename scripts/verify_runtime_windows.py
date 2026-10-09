"""Safe repeat-index, ownership and restart-persistence probes; no model calls."""
from datetime import datetime, timezone
import json
import subprocess
import time
import urllib.error
from run_fullstack_windows import LOCAL, DOCKER, SOURCE, configuration, call


def main():
    env = configuration()
    checks = []
    def check(name, passed, detail=None):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
    user = json.loads((LOCAL / "session.json").read_text(encoding="utf-8"))
    headers = {"Authorization": "Bearer " + user["access_token"]}
    captures = json.loads((LOCAL / "query-captures-extractive.json").read_text(encoding="utf-8"))
    history, _ = call("/api/v1/agent/sessions/" + captures["first"]["session_id"], env, headers=headers)
    check("history_survived_frontend_and_agent_restart", [row["role"] for row in history["messages"]]
          == ["user", "assistant", "user", "assistant"])
    other, _ = call("/api/v1/auth/tokens", env, body={"owner_id": "windows-verification-other-user", "expires_in_hours": 1}, admin=True)
    try:
        denied = False
        try:
            call("/api/v1/agent/sessions/" + captures["first"]["session_id"], env,
                 headers={"Authorization": "Bearer " + other["access_token"]})
        except urllib.error.HTTPError as error:
            denied = error.code in {403, 404}
        check("other_user_cannot_read_session", denied)
    finally:
        call("/api/v1/auth/tokens/" + other["id"], env, method="DELETE", admin=True)
    events, _ = call("/api/v1/knowledge-events?status=indexed&page_size=1", env)
    event = events["items"][0]
    previous_index = event["detail"]["index_id"]
    service_headers = {"Authorization": "Bearer " + env["AGENT_SERVICE_TOKEN"]}
    health, _ = call("/v1/health", env, base="http://127.0.0.1:9001", headers=service_headers)
    count_before = health["backend_documents"]["real"]
    call("/api/v1/knowledge-events/" + event["id"] + "/replay", env, method="POST", admin=True)
    deadline = time.monotonic() + 60
    current = None
    while time.monotonic() < deadline:
        indexed, _ = call("/api/v1/knowledge-events?status=indexed&page_size=100", env)
        current = next((row for row in indexed["items"] if row["id"] == event["id"]), None)
        if current:
            break
        time.sleep(1)
    check("replayed_event_real_index_ack", current is not None and current["detail"].get("index_id") == previous_index)
    after, _ = call("/v1/health", env, base="http://127.0.0.1:9001", headers=service_headers)
    check("repeat_index_does_not_duplicate_document", after["backend_documents"]["real"] == count_before)
    proof, _ = call("/v1/indexes/verify", env, body={"schema_version": "1", "document_id": event["document_id"],
        "content_hash": event["content_hash"], "index_id": previous_index, "operation": event["operation"], "is_demo": False},
        base="http://127.0.0.1:9001", headers=service_headers)
    check("replayed_index_remains_queryable", proof["queryable"] is True)
    backup = next(LOCAL.glob("pre-integration-*.dump"))
    result = subprocess.run([str(DOCKER), "compose", "-f", str(SOURCE / "compose.yaml"), "exec", "-T", "postgres", "pg_restore", "--list"],
        cwd=SOURCE, input=backup.read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check("pre_change_backup_archive_readable", result.returncode == 0)
    result = subprocess.run([str(DOCKER), "ps", "--filter", "label=com.docker.compose.project=cit-backend-development",
        "--filter", "label=com.docker.compose.service=beat", "--format", "{{.Names}}"], stdout=subprocess.PIPE, check=True, text=True)
    check("one_beat_only", len(result.stdout.splitlines()) == 1)
    result = subprocess.run([str(DOCKER), "compose", "-f", str(SOURCE / "compose.yaml"), "exec", "-T", "worker",
        "celery", "-A", "backend.tasks.celery_app:celery_app", "inspect", "ping", "--timeout", "5"],
        cwd=SOURCE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check("real_celery_worker_ping", result.returncode == 0 and "pong" in result.stdout.lower())
    docs, _ = call("/api/v1/documents?page_size=1", env)
    baseline = json.loads((LOCAL / "validation-extractive.json").read_text(encoding="utf-8"))
    check("original_document_count_preserved", docs["total"] >= baseline["real_documents"], {"real_documents": docs["total"]})
    failed, _ = call("/api/v1/knowledge-events?status=failed&page_size=1", env)
    check("no_failed_real_index_events", failed["total"] == 0, {"failed": failed["total"]})
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "passed": sum(row["passed"] for row in checks),
              "total": len(checks), "agent_documents": after["backend_documents"], "checks": checks,
              "limits": ["PostgreSQL itself was not restarted by this verifier; backend/Agent/frontend service restart persistence was checked"]}
    (LOCAL / "validation-runtime.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(row["passed"] for row in checks) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Runtime verification incomplete; inspect project logs. No credentials printed")
        raise SystemExit(1)
