"""Real deployed backend checks; secrets and generated answers are never printed.

DeepSeek requests are real and bounded to two turns. No external Agent or embedding
response is fabricated when those services are not configured.
"""
import json
import sys
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.api.v1.operations import distribution


def main():
    settings = get_settings()
    report = {"scope": "actual deployed HTTP", "checks": {}, "latency_definition":
        "client request to complete HTTP response; first token is not measured, sequential local load only"}
    token_id = None
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", timeout=45, trust_env=False) as client:
        admin = {"X-API-Key": settings.admin_api_key.get_secret_value()}
        tool = {"X-Agent-Token": settings.agent_tool_token.get_secret_value()}
        report["checks"]["health_ready"] = client.get("/health/ready").status_code == 200
        report["checks"]["tool_unauthorized"] = client.get("/api/v1/tools/documents").status_code == 401
        durations = []
        for _ in range(10):
            started = time.perf_counter()
            response = client.get("/api/v1/tools/documents", params={"query": "AI", "page_size": 10}, headers=tool)
            response.raise_for_status()
            durations.append(round((time.perf_counter() - started) * 1000, 3))
        report["tool_complete_response_ms"] = distribution(durations)
        report["checks"]["read_tool_cannot_write"] = client.post(
            "/api/v1/internal/knowledge-events/claim", json={}, headers=tool).status_code == 401
        proof = client.post("/api/v1/auth/tokens", headers=admin,
            json={"owner_id": "runtime-verification", "expires_in_hours": 1})
        proof.raise_for_status()
        private = proof.json()
        token_id = private["id"]
        user = {"Authorization": "Bearer " + private["access_token"]}
        try:
            response = client.post("/api/v1/agent/query", headers=user, json={"query": "torch"})
            report["agent_gateway"] = {"http_status": response.status_code,
                "configured": bool(settings.agent_service_url), "real_agent_completed": response.status_code == 200}
            if not settings.agent_service_url:
                report["checks"]["agent_missing_is_explicit"] = response.status_code == 503 and (
                    response.json()["error"]["code"] == "agent_service_not_configured")
            response = client.post("/api/v1/qa/semantic-search", headers=admin, json={"question": "AI security"})
            report["semantic_search"] = {"http_status": response.status_code,
                "embedding_configured": settings.embedding_configured}
            if not settings.embedding_configured:
                report["checks"]["embedding_missing_is_explicit"] = response.status_code == 503 and (
                    response.json()["error"]["code"] == "embedding_not_configured")
            if settings.deepseek_api_key and settings.qa_provider != "external":
                created = client.post("/api/v1/qa/conversations", headers=admin, json={"is_demo": False})
                created.raise_for_status()
                conversation_id = created.json()["id"]
                turns = []
                for question in ("torch 相关漏洞的影响是什么？请仅依据现有证据回答。", "这些 torch 问题有什么修复建议？证据不足请明确说明。"):
                    started = time.perf_counter()
                    response = client.post("/api/v1/qa/ask", headers=admin,
                        json={"question": question, "conversation_id": conversation_id})
                    duration = round((time.perf_counter() - started) * 1000, 3)
                    item = {"http_status": response.status_code, "complete_duration_ms": duration}
                    if response.status_code == 200:
                        data = response.json()
                        item.update({"status": data["status"], "citations": len(data["citations"]),
                                     "retrieval_mode": data["retrieval_mode"]})
                    else:
                        item["error_code"] = response.json().get("error", {}).get("code")
                    turns.append(item)
                history = client.get(f"/api/v1/qa/conversations/{conversation_id}", headers=admin).json()
                report["deepseek"] = {"turns": turns, "stored_turns": history["turn_count"],
                    "stored_messages": len(history["messages"]), "accuracy": None,
                    "accuracy_note": "Transport, evidence and persistence verified; generated claims not independently scored"}
                report["checks"]["deepseek_two_turn_http"] = all(t["http_status"] == 200 for t in turns)
                report["checks"]["deepseek_two_turn_history"] = history["turn_count"] == 2 and len(history["messages"]) == 4
        finally:
            if token_id:
                revoked = client.delete(f"/api/v1/auth/tokens/{token_id}", headers=admin)
                report["checks"]["temporary_token_revoked"] = revoked.status_code == 200 and (
                    client.get("/api/v1/agent/sessions", headers=user).status_code == 401)
        report["metrics"] = client.get("/api/v1/ops/metrics", headers=admin).json()
    target = ROOT / "output" / "runtime-verification.json"
    target.parent.mkdir(exist_ok=True)
    report["passed"] = all(report["checks"].values())
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "metrics"}, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
