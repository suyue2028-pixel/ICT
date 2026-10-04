"""Real localhost integration probes; never substitutes backend or embeddings.

Requires scripts/run_fullstack.py start and at least one real indexed source.
Writes credential-free results to reports/local/fullstack/validation.json.
"""
from __future__ import annotations
import json
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4

from run_fullstack import ROOT, LOCAL, OPENER, configuration, request


def main():
    env = configuration()
    checks = []
    captures = {}

    def check(name, condition, detail=None):
        checks.append({"name": name, "passed": bool(condition), "detail": detail})

    def call(url, *, body=None, headers=None, method=None):
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        payload = json.dumps(body).encode() if body is not None else None
        try:
            with OPENER.open(urllib.request.Request(url, data=payload, headers=request_headers,
                            method=method), timeout=40) as response:
                raw = response.read(2 * 1024 * 1024)
                return response.status, json.loads(raw), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, {}, {}

    data, _ = request("/health/ready", env)
    check("real_postgres_redis_migration_ready", data.get("status") == "ready" and all(data["checks"].values()), data)
    status, session, headers = call("http://127.0.0.1:5173/__local/session")
    check("frontend_dev_user_session", status == 200 and bool(session.get("access_token")))
    check("session_not_cacheable", headers.get("Cache-Control") == "no-store")
    denied, _, _ = call("http://127.0.0.1:5173/__local/session", headers={"Origin": "https://example.org"})
    check("cross_origin_session_denied", denied == 403)
    user_headers = {"Authorization": "Bearer " + session["access_token"]}
    docs, _ = request("/api/v1/documents?page_size=100", env)
    check("real_documents_collected", docs["total"] > 0, {"total": docs["total"]})
    status, frontend_docs, _ = call("http://127.0.0.1:5173/api/v1/documents?page_size=100")
    check("vite_proxy_reads_real_backend", status == 200 and frontend_docs == docs)
    events, _ = request("/api/v1/knowledge-events?page_size=100", env)
    check("real_index_events_acknowledged", len(events["items"]) > 0 and all(
          item["status"] == "indexed" for item in events["items"]), {"total": events["total"]})
    service_headers = {"Authorization": "Bearer " + env["AGENT_SERVICE_TOKEN"]}
    status, health, _ = call("http://127.0.0.1:9001/v1/health", headers=service_headers)
    check("agent_real_e5_health", status == 200 and health.get("embedding_mode") != "mock", health)
    if status != 200 or health.get("llm_mode") != "extractive":
        raise RuntimeError("This verifier requires extractive mode; no query was sent to a live model.")
    current = {(row["id"], row["content_hash"]) for row in docs["items"]}
    verified_events = [row for row in events["items"] if row["status"] == "indexed"
        and row["operation"] in {"add", "update"} and row.get("detail", {}).get("index_id")
        and (row["document_id"], row["content_hash"]) in current]
    if not verified_events:
        raise RuntimeError("No current indexed real document; seed sources and wait for collection/sync.")
    for event in verified_events[:3]:
        status, proof, _ = call("http://127.0.0.1:9001/v1/indexes/verify", headers=service_headers,
            body={"schema_version": "1", "document_id": event["document_id"], "content_hash": event["content_hash"],
                  "index_id": event["detail"]["index_id"], "is_demo": False, "operation": event["operation"]})
        check("real_index_proof_" + event["document_id"], status == 200 and proof.get("queryable") is True)
    vulns, _ = request("/api/v1/intelligence?page_size=100", env)
    target = next((row for row in vulns["items"] if row["identifier"].startswith("CVE-")), None)
    if target is None:
        raise RuntimeError("No real CVE source; seed OSV and wait for collection/sync.")
    query = {"query": target["identifier"] + " 的相关安全资料和风险", "session_id": None, "is_demo": False}
    key = str(uuid4())
    started = time.monotonic()
    status, first, first_headers = call("http://127.0.0.1:5173/api/v1/agent/query", body=query,
                                     headers={**user_headers, "Idempotency-Key": key})
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    captures["first"] = first
    check("frontend_proxy_backend_agent_query", status == 200 and first.get("status") in {"completed", "partial"},
          {"http": status, "duration_ms": duration_ms, "target": target["identifier"]})
    check("backend_accepted_real_sources", bool(first.get("sources")) and bool(first.get("evidence")))
    check("extractive_mode_disclosed", any("generation_mode:extractive" in item for item in first.get("partial_errors", [])))
    lowered_headers = {key.lower(): value for key, value in first_headers.items()}
    check("gateway_preserves_real_generation_headers", lowered_headers.get("x-rag-answer-mode") == "extractive"
          and lowered_headers.get("x-rag-embedding-mode") == "live" and lowered_headers.get("x-rag-llm-calls") == "0")
    check("structured_source_quotes_pass_revision_validation", bool(first.get("evidence"))
          and "citation_excerpt_not_in_backend_revision" not in first.get("partial_errors", [])
          and any(row["locator"].startswith("revision_json:") for row in first.get("evidence", [])))
    check("answer_uses_public_citation_numbers", not re.search(r"doc_[a-f0-9]{24}", first.get("answer", "")))
    for index, evidence in enumerate(first.get("evidence", [])):
        original, _ = request("/api/v1/documents/" + evidence["document_id"], env)
        revision = next(row["payload"] for row in original["revisions"] if row["content_hash"] == evidence["content_hash"])
        locator_type, _, start, end = evidence["locator"].split(":")
        text = original["content"] if locator_type == "content" else json.dumps(revision, ensure_ascii=False)
        check("original_excerpt_and_locator_" + str(index), text[int(start):int(end)] == evidence["content"]
              and original["content_hash"] == evidence["content_hash"] and not original["is_demo"])
    status, duplicate, _ = call("http://127.0.0.1:5173/api/v1/agent/query", body=query,
                               headers={**user_headers, "Idempotency-Key": key})
    check("idempotent_same_response", status == 200 and duplicate == first)
    follow_query = {"query": "这个漏洞的受影响范围是什么？", "session_id": first.get("session_id"), "is_demo": False}
    status, follow, _ = call("http://127.0.0.1:5173/api/v1/agent/query", body=follow_query, headers=user_headers)
    captures["follow"] = follow
    check("followup_same_backend_session", status == 200 and follow.get("session_id") == first.get("session_id"))
    status, history, _ = call("http://127.0.0.1:5173/api/v1/agent/sessions/" + first["session_id"], headers=user_headers)
    check("backend_persisted_four_messages", status == 200 and len(history.get("messages", [])) == 4)
    check("history_order_and_restore_fields", [row["role"] for row in history.get("messages", [])]
          == ["user", "assistant", "user", "assistant"]
          and all(row.get("id") and row.get("created_at") for row in history.get("messages", [])))
    status, _, _ = call("http://127.0.0.1:5173/api/v1/agent/query", body=query)
    check("query_requires_user_token", status == 401)
    status, _, _ = call("http://127.0.0.1:9001/v1/health", headers={"Authorization": "Bearer incorrect"})
    check("agent_requires_service_token", status == 401)
    demo_query = {**query, "is_demo": True}
    status, demo, _ = call("http://127.0.0.1:5173/api/v1/agent/query", body=demo_query, headers=user_headers)
    check("real_documents_not_leaked_to_demo", status == 200 and not demo.get("sources"))
    result = {"date": "2026-10-04", "backend": "real PostgreSQL/Redis/Celery/HTTP",
              "embedding": "real multilingual-e5-small CPU", "generation": "extractive", "remote_llm_calls": 0,
              "real_documents": docs["total"], "indexed_events": events["total"],
              "passed": sum(row["passed"] for row in checks), "total": len(checks), "checks": checks,
              "limitations": ["No real DeepSeek call or independent answer quality review in this run"]}
    (LOCAL / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (LOCAL / "query_captures.json").write_text(json.dumps(captures, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(row["passed"] for row in checks) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError, KeyError, ValueError) as error:
        print("Verification could not complete: " + str(error))
        raise SystemExit(1)
