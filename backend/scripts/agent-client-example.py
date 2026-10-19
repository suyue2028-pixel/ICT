"""User-side example: local user token, never ADMIN_API_KEY or a model key."""
import os
import httpx

base = os.environ.get("BACKEND_URL", "http://127.0.0.1:8010")
token = os.environ.get("BACKEND_USER_TOKEN")
if not token:
    raise SystemExit("Set BACKEND_USER_TOKEN privately; obtain a user token from the administrator")
with httpx.Client(base_url=base, timeout=40, trust_env=False,
                  headers={"Authorization": "Bearer " + token}) as client:
    first = client.post("/api/v1/agent/query", json={"query": "torch 相关漏洞有哪些影响？"})
    if first.status_code == 503:
        print("Agent unavailable:", first.json().get("error", {}).get("code", "unknown_error"))
    else:
        first.raise_for_status()
        result = first.json()
        print(result["answer"])
        followup = client.post("/api/v1/agent/query", json={"query": "有什么修复建议？", "session_id": result["session_id"]})
        followup.raise_for_status()
        print(followup.json()["answer"])
