"""Attempt each real source independently and record sanitized outcomes."""
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.config import get_settings


def main():
    settings = get_settings()
    outcomes = {}
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}",
                      timeout=15, trust_env=False) as client:
        sources = {item["adapter"]: item for item in client.get("/api/v1/sources").json()}
        headers = {"X-API-Key": settings.admin_api_key.get_secret_value()}
        pending = {}
        for adapter in sources:
            if adapter == "offline":
                continue
            response = client.post(f"/api/v1/sources/{sources[adapter]['id']}/collect",
                                   headers=headers)
            if response.status_code == 202:
                pending[adapter] = response.json()["id"]
                outcomes[adapter] = {"accepted": True, "task_id": pending[adapter]}
            else:
                outcomes[adapter] = {"accepted": False, "http_status": response.status_code}
        deadline = time.monotonic() + 240
        while pending and time.monotonic() < deadline:
            for adapter, task_id in list(pending.items()):
                try:
                    response = client.get(f"/api/v1/tasks/{task_id}")
                    response.raise_for_status()
                    task = response.json()
                except httpx.HTTPError:
                    continue
                if task["status"] in {"succeeded", "failed"}:
                    outcomes[adapter].update({
                        "status": task["status"], "attempts": task["attempts"],
                        "error_code": task["error_code"], "result": task["result"],
                    })
                    del pending[adapter]
            if pending:
                time.sleep(3)
        for adapter in pending:
            outcomes[adapter]["status"] = "timed_out_waiting"
        for adapter in outcomes:
            outcomes[adapter]["category"] = sources[adapter]["category"]
    path = ROOT / "output" / "live-sources.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(outcomes, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
