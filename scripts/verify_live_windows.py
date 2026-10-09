"""Two bounded real DeepSeek turns through the Vite/backend/Agent chain."""
from datetime import datetime, timezone
import json
import time
from uuid import uuid4
from run_fullstack_windows import LOCAL, configuration, call


def main():
    env = configuration()
    session = json.loads((LOCAL / "session.json").read_text(encoding="utf-8"))
    user = {"Authorization": "Bearer " + session["access_token"]}
    previous = json.loads((LOCAL / "query-captures-extractive.json").read_text(encoding="utf-8"))
    cve = previous["first"]["cve_ids"][0]
    query = {"query": cve + " 的风险有哪些？请根据来源用中文说明。", "is_demo": False}
    key = str(uuid4())
    results, captures = [], {}
    started = time.monotonic()
    first, headers = call("/api/v1/agent/query", env, body=query, headers={**user, "Idempotency-Key": key}, base="http://127.0.0.1:5173")
    header = {k.lower(): v for k, v in headers.items()}
    captures["first"] = first
    results.append({"name": "actual_deepseek_generation", "passed": header.get("x-rag-answer-mode") == "live"
        and int(header.get("x-rag-llm-calls", "0")) > 0 and bool(first["sources"]),
        "status": first["status"], "answer_mode": header.get("x-rag-answer-mode"), "llm_http_calls": header.get("x-rag-llm-calls"),
        "duration_ms": round((time.monotonic() - started) * 1000, 2), "sources": len(first["sources"]),
        "partial_errors": first["partial_errors"]})
    duplicate, _ = call("/api/v1/agent/query", env, body=query, headers={**user, "Idempotency-Key": key}, base="http://127.0.0.1:5173")
    results.append({"name": "live_idempotency_same_answer", "passed": duplicate == first})
    follow, follow_headers = call("/api/v1/agent/query", env, body={"query": "这个漏洞的受影响范围和修复建议是什么？",
        "session_id": first["session_id"], "is_demo": False}, headers=user, base="http://127.0.0.1:5173")
    captures["follow"] = follow
    h = {k.lower(): v for k, v in follow_headers.items()}
    results.append({"name": "live_followup_same_session", "passed": follow["session_id"] == first["session_id"],
        "status": follow["status"], "answer_mode": h.get("x-rag-answer-mode"), "llm_http_calls": h.get("x-rag-llm-calls"),
        "sources": len(follow["sources"]), "partial_errors": follow["partial_errors"]})
    history, _ = call("/api/v1/agent/sessions/" + first["session_id"], env, headers=user)
    results.append({"name": "live_four_messages_persisted", "passed": [r["role"] for r in history["messages"]]
        == ["user", "assistant", "user", "assistant"]})
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "cve": cve, "passed": sum(r["passed"] for r in results),
              "total": len(results), "checks": results}
    (LOCAL / "validation-live-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (LOCAL / "query-captures-live.json").write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Live smoke verification incomplete; inspect service logs. No credentials printed")
        raise SystemExit(1)
