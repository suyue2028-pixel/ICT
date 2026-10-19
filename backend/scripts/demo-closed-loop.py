"""Auditable backend/Agent demo. --ask makes at most TWO real Agent requests."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx
from sqlalchemy import select, func

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import Document, Vulnerability, VulnerabilityDocument


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=["demo", "real"], default="demo")
    parser.add_argument("--ask", action="store_true", help="Call the configured real Agent and model, bounded to two turns")
    parser.add_argument("--via", default="http://127.0.0.1:8010", choices=["http://127.0.0.1:8010", "http://127.0.0.1:5173"])
    args = parser.parse_args()
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / "output/traceability" / ("demo-" + args.scope + "-" + stamp)
    folder.mkdir(parents=True, exist_ok=False)
    checks, captures = [], {}
    report = {"scope": args.scope, "is_demo": args.scope == "demo", "timestamp": datetime.now(timezone.utc).isoformat(),
        "backend_entry": args.via + "/api/v1/agent/query", "real_model_calls_reported": 0,
        "answer_quality_independently_scored": False, "checks": checks}
    def check(name, passed, **detail):
        checks.append({"name": name, "passed": bool(passed), **detail})
        if not passed:
            raise RuntimeError("Demo check failed: " + name)
    token_id, user = None, None
    with httpx.Client(base_url=args.via, timeout=125, trust_env=False, follow_redirects=False) as client:
        admin = {"X-API-Key": settings.admin_api_key.get_secret_value()}
        try:
            check("api_ready", client.get("/health/ready").status_code == 200)
            if args.scope == "demo":
                sources = client.get("/api/v1/sources").json()
                source = next((s for s in sources if s["name"] == "[离线虚构]闭环演示" and s["adapter"] == "offline"), None)
                if source is None:
                    r = client.post("/api/v1/sources", headers=admin,
                        json={"name": "[离线虚构]闭环演示", "adapter": "offline"})
                    r.raise_for_status()
                    source = r.json()
                key = "closed-loop-" + uuid4().hex
                response = client.post(f"/api/v1/sources/{source['id']}/collect", headers={**admin, "Idempotency-Key": key})
                check("api_accepted_outbox_task", response.status_code == 202)
                task_id = response.json()["id"]
                deadline = time.monotonic() + 100
                while True:
                    task = client.get("/api/v1/tasks/" + task_id).json()
                    if task["status"] in {"succeeded", "failed"}:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError("Demo collection timeout")
                    time.sleep(1)
                captures["collection_task"] = task
                check("redis_worker_ingestion", task["status"] == "succeeded")
                repeated = client.post(f"/api/v1/sources/{source['id']}/collect", headers={**admin, "Idempotency-Key": key}).json()
                check("idempotent_task", repeated["id"] == task_id)
                docs = client.get("/api/v1/documents", params={"source_id": source["id"], "is_demo": "true"}).json()["items"]
                check("fictional_documents_isolated", len(docs) == 3 and all(d["is_demo"] for d in docs))
                cve = "CVE-2099-10001"  # Explicitly fictional fixture, never a real collection result.
                subject = None
            else:
                with get_session_factory()() as db:
                    choice = db.execute(select(Vulnerability.id, Vulnerability.identifier,
                        func.count(func.distinct(Document.source_id))).join(VulnerabilityDocument,
                        VulnerabilityDocument.vulnerability_id == Vulnerability.id).join(Document,
                        Document.id == VulnerabilityDocument.document_id).where(Vulnerability.is_demo.is_(False),
                        Vulnerability.identifier.startswith("CVE-"), Document.is_demo.is_(False),
                        Document.deleted_at.is_(None), Document.ai_relevant.is_(True), Document.indexed_at.is_not(None))
                        .group_by(Vulnerability.id).having(func.count(func.distinct(Document.source_id)) >= 2)
                        .order_by(func.count(func.distinct(Document.source_id)).desc(), Vulnerability.identifier).limit(1)).first()
                    if not choice:
                        raise RuntimeError("No real indexed multi-source CVE available; no demo substitute used")
                    subject, cve = str(choice[0]), choice[1]
                report["multi_source_count"] = choice[2]
                docs = None
            report["cve"] = cve
            tools = {"X-Agent-Token": settings.agent_tool_token.get_secret_value()}
            detail = client.get(f"/api/v1/tools/cves/{cve}", params={"is_demo": args.scope == "demo"}, headers=tools)
            detail.raise_for_status()
            references = detail.json()["data"]["references"]
            captures["cve_detail"] = detail.json()
            check("multiple_documents_auto_associated", len(references) >= 2)
            associations = []
            for ref in references[:10]:
                proof = client.get(f"/api/v1/documents/{ref['document_id']}/associations").json()
                associations.append(proof)
            captures["associations"] = associations
            check("versioned_association_basis", all(p["items"] and any(i["active_link"] for i in p["items"]) for p in associations))
            report["association_origins"] = sorted({i["origin"] for p in associations for i in p["items"]})
            if subject:
                relations = client.get("/api/v1/relations/" + subject).json()
                captures["relations"] = relations
                check("relation_edges_have_proof", bool(relations["edges"]) and all(e["evidence"]["associations"] for e in relations["edges"]))
            if args.ask:
                if args.scope == "demo":
                    target = next(r for r in references if r["ai_relevant"])
                    deadline = time.monotonic() + 180
                    while True:
                        d = client.get(f"/api/v1/documents/{target['document_id']}").json()
                        if d.get("indexed_at"):
                            break
                        if time.monotonic() > deadline:
                            raise RuntimeError("Demo index not acknowledged; retain pending state, no fake success")
                        time.sleep(2)
                    check("demo_current_index_acknowledged", bool(d["indexed_at"]))
                created = client.post("/api/v1/auth/tokens", headers=admin,
                    json={"owner_id": "closed-loop-" + uuid4().hex[:12], "expires_in_hours": 1})
                created.raise_for_status()
                token_id = created.json()["id"]
                user = {"Authorization": "Bearer " + created.json()["access_token"]}
                conversation_id = None
                for turn, question in enumerate((f"{cve} 的主要风险是什么？请引用来源。",
                                                 "它影响哪些产品和版本，如何修复？只回答有证据的部分。"), 1):
                    body = {"query": question, "is_demo": args.scope == "demo"}
                    if conversation_id:
                        body["session_id"] = conversation_id
                    started = time.monotonic()
                    answer = client.post("/api/v1/agent/query", headers={**user, "Idempotency-Key": uuid4().hex}, json=body)
                    check(f"turn_{turn}_http", answer.status_code == 200)
                    data = answer.json()
                    captures[f"turn_{turn}"] = data
                    count = data.get("llm_http_calls")
                    report["real_model_calls_reported"] += count if type(count) is int else 0
                    check(f"turn_{turn}_real_generation", data.get("generation_mode") == "live" and type(count) is int and count > 0,
                        status=data["status"], llm_http_calls=count, duration_ms=round((time.monotonic()-started)*1000))
                    conversation_id = data["session_id"]
                    trace = client.get(f"/api/v1/agent/invocations/{data['invocation_id']}/evidence", headers=user)
                    trace.raise_for_status()
                    proof = trace.json()
                    captures[f"evidence_{turn}"] = proof
                    check(f"turn_{turn}_quotes_and_versions_verified", proof["all_quotes_verified"] and proof["all_versions_current"])
                    for item in proof["evidence"]:
                        version = client.get(item["version_url"])
                        version.raise_for_status()
                        representation = version.json()[item["position"]["representation"]]
                        check(f"turn_{turn}_original_quote_{item['number']}",
                            representation[item["position"]["start"]:item["position"]["end"]] == item["excerpt"])
                    check(f"turn_{turn}_closed_loop_association", any(
                        p["active_link"] for e in proof["evidence"] for p in e["associations"]))
                history = client.get("/api/v1/agent/sessions/" + conversation_id, headers=user).json()
                check("two_turns_four_ordered_messages", [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"])
            else:
                report["agent_validation"] = "not_requested; association/pipeline only, no simulated answer presented"
        except Exception as error:
            report["error_type"] = type(error).__name__
        finally:
            if token_id:
                try:
                    revoked = client.delete("/api/v1/auth/tokens/" + token_id, headers=admin)
                    check("temporary_user_revoked", revoked.status_code == 200)
                except Exception:
                    report["token_revocation_incomplete"] = True
    report["passed"] = (bool(checks) and all(c["passed"] for c in checks)
        and "error_type" not in report and not report.get("token_revocation_incomplete"))
    report["closed_loop_verified"] = args.ask and report["passed"]
    report["validated_stages"] = (["ingestion", "association", "version_query"] if args.scope == "demo"
        else ["existing_real_multi_source_association", "version_query"])
    if args.ask and report["passed"]:
        report["validated_stages"] += ["real_agent_generation", "original_evidence", "two_turn_history"]
    (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "captures.json").write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 自动关联 → Agent 回答 → 原文证据演示", "", f"数据范围：{args.scope}；时间：{report['timestamp']}", "",
        "虚构数据只用于流程演示，不属于真实采集成绩。" if args.scope == "demo" else "使用已有真实、多来源且已索引的漏洞资料。",
        "", f"演示通过：{report['passed']}；模型 HTTP 调用（服务报告）：{report['real_model_calls_reported']}", "",
        "引用完整性通过不能代替人工对每条结论的支持性审阅。", "", "| 检查 | 结果 |", "|---|---|"]
    lines += [f"| {c['name']} | {'通过' if c['passed'] else '失败'} |" for c in checks]
    for turn in (1, 2):
        if f"turn_{turn}" in captures:
            value = captures[f"turn_{turn}"]
            lines += ["", f"## 第 {turn} 轮回答", "", value["answer"], "", f"调用记录：`{value['invocation_id']}`"]
            for quote in captures.get(f"evidence_{turn}", {}).get("evidence", []):
                lines += ["", f"- 原文：{quote['source_url']}", f"- 文档：`{quote['document_id']}`，版本：`{quote['content_hash']}`",
                    f"- 定位：`{quote['locator']}`", "", "> " + quote["excerpt"].replace("\n", "\n> ")]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(checks), "scope": args.scope,
        "model_calls": report["real_model_calls_reported"], "error_type": report.get("error_type"), "report": str(folder / "report.md")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
