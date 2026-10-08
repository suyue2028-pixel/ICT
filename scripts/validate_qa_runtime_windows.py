"""Validate persisted gateway diagnostics, history and service readiness."""
import json
from datetime import datetime, timezone
from run_fullstack_windows import LOCAL, configuration, call

def main():
    env = configuration()
    token = json.loads((LOCAL / "session.json").read_text(encoding="utf-8"))["access_token"]
    headers = {"Authorization": "Bearer " + token}
    captured = json.loads((LOCAL / "qa-relevance-after.json").read_text(encoding="utf-8"))
    rows = {row["name"]: row for row in captured["rows"]}
    checks = []
    for name in ["access", "audit", "exact", "follow", "follow2", "switch", "absent"]:
        row = rows.get(name, {})
        result = row.get("result", {})
        good = not row.get("error") and result.get("generation_mode") == row.get("mode") and str(result.get("llm_http_calls")) == row.get("llm_calls")
        persisted, _ = call("/api/v1/agent/invocations/" + result["invocation_id"], env, headers=headers)
        checks.append({"name": name + "_diagnostics", "passed": good and persisted.get("generation_mode") == result.get("generation_mode")
            and persisted.get("llm_http_calls") == result.get("llm_http_calls"), "mode": row.get("mode"), "llm_calls": row.get("llm_calls")})
    history, _ = call("/api/v1/agent/sessions/" + rows["exact"]["result"]["session_id"], env, headers=headers)
    checks.append({"name":"history_order", "passed": [m["role"] for m in history["messages"]] == ["user", "assistant"] * 4})
    checks.append({"name":"three_turns_same_target", "passed": all(rows[n]["result"]["cve_ids"] == ["CVE-2026-73558"] for n in ["exact", "follow", "follow2"])})
    checks.append({"name":"topic_switch_drops_previous_cve", "passed": "CVE-2026-73558" not in rows["switch"]["result"]["cve_ids"]})
    checks.append({"name":"absent_does_not_generate", "passed": rows["absent"]["result"]["status"] == "insufficient_evidence" and not rows["absent"]["result"]["sources"] and rows["absent"]["llm_calls"] == "0"})
    for name in ["access", "audit"]:
        checks.append({"name":name+"_no_irrelevant_regulation", "passed": all("生成式人工智能服务管理暂行办法" != s["title"] for s in rows[name]["result"]["sources"])})
    ready, _ = call("/health/ready", env)
    checks.append({"name":"backend_ready", "passed": ready.get("status") in {"ok", "ready"}})
    health, _ = call("/v1/health", env, base="http://127.0.0.1:9001", headers={"Authorization":"Bearer "+env["AGENT_SERVICE_TOKEN"]})
    checks.append({"name":"agent_ready", "passed":health.get("status") == "ok"})
    report = {"time":datetime.now(timezone.utc).isoformat(), "checks":checks, "passed":sum(c["passed"] for c in checks), "total":len(checks),
        "scope":"Protocol, topic consistency and diagnostics checks; semantic quality requires evidence review."}
    (LOCAL / "qa-runtime-validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if all(c["passed"] for c in checks) else 1

if __name__ == "__main__":
    raise SystemExit(main())
