"""Actual Windows HTTP chain; secrets stay in process memory and ignored files."""
from datetime import datetime, timezone
import json
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4
from run_fullstack_windows import ROOT, LOCAL, OPENER, configuration, call


def main():
    env = configuration()
    checks, captures = [], {}

    def check(name, passed, detail=None):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    def request(path, *, body=None, headers=None, base="http://127.0.0.1:5173"):
        h = {"Content-Type": "application/json", **(headers or {})}
        payload = json.dumps(body).encode() if body is not None else None
        try:
            with OPENER.open(urllib.request.Request(base + path, data=payload, headers=h), timeout=40) as response:
                return response.status, json.loads(response.read(2 * 1024 * 1024)), {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as error:
            raw = error.read(4096)
            try:
                detail = json.loads(raw)
            except ValueError:
                detail = {}
            return error.code, detail, {}

    ready, _ = call("/health/ready", env)
    check("postgres_redis_migration_ready", ready.get("status") == "ready" and all(ready["checks"].values()))
    docs, _ = call("/api/v1/documents?page_size=3", env)
    status, proxy_docs, _ = request("/api/v1/documents?page_size=3")
    check("frontend_proxy_real_documents", status == 200 and docs == proxy_docs, {"real_documents": docs["total"]})
    for endpoint in ["/api/v1/sources", "/api/v1/tasks?page_size=2", "/api/v1/intelligence?page_size=2", "/api/v1/stats"]:
        status, _, _ = request(endpoint)
        check("frontend_proxy_" + endpoint, status == 200)
    status, session, headers = request("/__local/session")
    check("ordinary_frontend_session", status == 200 and bool(session.get("access_token")))
    check("user_token_not_cacheable", headers.get("cache-control") == "no-store")
    status, _, _ = request("/__local/session", headers={"Origin": "https://example.org"})
    check("cross_origin_token_request_denied", status == 403)
    user = {"Authorization": "Bearer " + session["access_token"]}
    service = {"Authorization": "Bearer " + env["AGENT_SERVICE_TOKEN"]}
    status, health, _ = request("/v1/health", headers=service, base="http://127.0.0.1:9001")
    check("authenticated_real_agent_health", status == 200 and health.get("embedding_provider") == "e5", health)
    selected = None
    events, _ = call("/api/v1/knowledge-events?status=indexed&page_size=100", env)
    for event in events["items"]:
        document, _ = call("/api/v1/documents/" + event["document_id"], env)
        if document["content_hash"] != event["content_hash"] or document.get("deleted_at"):
            continue
        aliases = [document["external_id"], *document.get("raw_payload", {}).get("aliases", [])]
        cve = next((item for item in aliases if re.fullmatch(r"CVE-\d{4}-\d+", item, re.I)), None)
        if cve and event.get("detail", {}).get("index_id"):
            selected = (event, document, cve)
            break
    if selected is None:
        raise RuntimeError("No current real indexed CVE; wait for sync, then rerun")
    event, document, cve = selected
    proof_body = {"schema_version": "1", "document_id": document["id"], "content_hash": document["content_hash"],
                  "index_id": event["detail"]["index_id"], "operation": event["operation"], "is_demo": False}
    status, proof, _ = request("/v1/indexes/verify", body=proof_body, headers=service, base="http://127.0.0.1:9001")
    check("actual_queryable_index_proof", status == 200 and proof.get("queryable") is True)
    query = {"query": cve + " 的相关安全资料和风险", "session_id": None, "is_demo": False}
    key = str(uuid4())
    started = time.monotonic()
    status, first, response_headers = request("/api/v1/agent/query", body=query, headers={**user, "Idempotency-Key": key})
    duration = round((time.monotonic() - started) * 1000, 2)
    captures["first"] = first
    check("frontend_backend_agent_answer", status == 200 and first.get("status") in {"completed", "partial"}, {"http": status, "cve": cve, "duration_ms": duration})
    check("real_sources_and_evidence", bool(first.get("sources")) and bool(first.get("evidence")))
    check("real_embedding_response_header", response_headers.get("x-rag-embedding-mode") == "live")
    mode, calls = response_headers.get("x-rag-answer-mode"), response_headers.get("x-rag-llm-calls")
    check("generation_mode_explicit", mode in {"live", "extractive"} and calls is not None, {"answer_mode": mode, "llm_calls": calls})
    for index, evidence in enumerate(first.get("evidence", [])):
        original, _ = call("/api/v1/documents/" + evidence["document_id"], env)
        revision = next((row["payload"] for row in original["revisions"] if row["content_hash"] == evidence["content_hash"]), None)
        kind, _, start, end = evidence["locator"].split(":")
        text = original["content"] if kind == "content" else json.dumps(revision, ensure_ascii=False)
        check("quote_version_and_locator_" + str(index), text[int(start):int(end)] == evidence["content"]
              and original["content_hash"] == evidence["content_hash"] and not original["is_demo"])
    status, duplicate, _ = request("/api/v1/agent/query", body=query, headers={**user, "Idempotency-Key": key})
    check("idempotent_query_same_response", status == 200 and duplicate == first)
    follow_query = {"query": "这个漏洞的受影响范围是什么？", "session_id": first.get("session_id"), "is_demo": False}
    status, follow, _ = request("/api/v1/agent/query", body=follow_query, headers=user)
    captures["follow"] = follow
    check("continuous_followup_same_session", status == 200 and follow.get("session_id") == first.get("session_id"))
    status, history, _ = request("/api/v1/agent/sessions/" + first["session_id"], headers=user)
    check("four_messages_persisted_in_postgres", status == 200 and [row["role"] for row in history.get("messages", [])] == ["user", "assistant", "user", "assistant"])
    check("history_restore_has_ids_and_times", all(row.get("id") and row.get("created_at") for row in history.get("messages", [])))
    status, hybrid, hybrid_headers = request("/api/v1/agent/query", body={**query,
        "query": "AI工具权限控制和提示注入相关的安全风险有哪些？"}, headers=user)
    captures["hybrid"] = hybrid
    check("real_semantic_hybrid_retrieval", status == 200 and hybrid.get("retrieval_mode") == "hybrid"
          and bool(hybrid.get("sources")) and hybrid_headers.get("x-rag-embedding-mode") == "live",
          {"http": status, "retrieval_mode": hybrid.get("retrieval_mode"), "sources": len(hybrid.get("sources", []))})
    status, _, _ = request("/api/v1/agent/query", body=query)
    check("unauthenticated_query_denied", status == 401)
    status, _, _ = request("/v1/health", headers={"Authorization": "Bearer invalid"}, base="http://127.0.0.1:9001")
    check("invalid_agent_service_token_denied", status == 401)
    status, demo, _ = request("/api/v1/agent/query", body={**query, "is_demo": True}, headers=user)
    check("real_cve_not_leaked_into_demo", status == 200 and not demo.get("sources"))
    status, empty, _ = request("/api/v1/agent/query", body={**query, "query": "CVE-2099-999999999 的资料"}, headers=user)
    check("missing_evidence_disclosed", status == 200 and empty.get("status") == "insufficient_evidence" and not empty.get("sources"))
    if mode == "extractive":
        check("extractive_not_claimed_as_model_generation", any("generation_mode:extractive" in item for item in first.get("partial_errors", [])))
    else:
        check("actual_remote_generation", mode == "live" and int(calls or "0") > 0)
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "backend": "actual Docker PostgreSQL/Redis/Celery",
              "embedding": "actual fixed multilingual-e5-small CPU", "generation": mode,
              "first_query_llm_http_calls": calls, "real_documents": docs["total"], "agent_documents": health.get("backend_documents"),
              "passed": sum(item["passed"] for item in checks), "total": len(checks), "checks": checks}
    suffix = "live" if mode == "live" else "extractive"
    (LOCAL / ("validation-" + suffix + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (LOCAL / ("query-captures-" + suffix + ".json")).write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(item["passed"] for item in checks) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, KeyError, ValueError, urllib.error.URLError):
        print("Verification incomplete; inspect service logs. No credentials printed")
        raise SystemExit(1)
