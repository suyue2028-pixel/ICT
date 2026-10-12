"""Actual API -> outbox -> Redis -> worker collection; no mock/model calls."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from backend.core.config import get_settings
from backend.collectors.registry import SOURCE_DEFINITIONS


def main():
    settings = get_settings()
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "output" / ("timeliness-live-" + stamp + ".json")
    report = {"started_at": started.isoformat(), "simulation": False, "sources": [], "checks": []}
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", timeout=15, trust_env=False) as client:
        admin = {"X-API-Key": settings.admin_api_key.get_secret_value()}
        def get(path, privileged=False):
            response = client.get(path, headers=admin if privileged else {})
            response.raise_for_status()
            return response.json()
        def check(name, condition):
            report["checks"].append({"name": name, "passed": bool(condition)})
        check("api_ready", get("/health/ready")["status"] == "ready")
        check("freshness_requires_auth", client.get("/api/v1/ops/freshness").status_code == 401)
        before = get("/api/v1/ops/freshness", True)
        sources = get("/api/v1/sources")
        for source in sources:
            if source["adapter"] == "offline":
                continue
            entry = {"adapter": source["adapter"], "source_id": source["id"],
                     "enabled": source["enabled"], "interval_seconds": source["interval_seconds"]}
            response = client.post(f"/api/v1/sources/{source['id']}/collect",
                headers={**admin, "Idempotency-Key": "freshness-" + stamp + "-" + source["adapter"]})
            if response.status_code == 202:
                entry["task_id"] = response.json()["id"]
            elif response.status_code == 429:
                # Reuse a genuine just-completed/in-flight task rather than evade shared limits.
                tasks = get(f"/api/v1/tasks?source_id={source['id']}&page_size=1")["items"]
                if tasks:
                    entry["task_id"] = tasks[0]["id"]
                    entry["reused_recent_task"] = True
            else:
                entry["trigger_error"] = response.json().get("error", {}).get("code", "http_" + str(response.status_code))
            report["sources"].append(entry)
        print(f"{len(report['sources'])} real source checks queued or reused; waiting for workers", flush=True)
        pending = [row for row in report["sources"] if row.get("task_id")]
        deadline = time.monotonic() + 600
        last_progress = 0
        while pending and time.monotonic() < deadline:
            for row in list(pending):
                task = get("/api/v1/tasks/" + row["task_id"])
                row.update({key: task[key] for key in ["status", "attempts", "started_at", "finished_at", "result", "error_code"]})
                if task["status"] in {"succeeded", "failed"}:
                    result, code = task.get("result", {}), task.get("error_code") or ""
                    row["outcome"] = ("success_with_new_or_updated" if result.get("created", 0) + result.get("updated", 0)
                                      else "success_no_change") if task["status"] == "succeeded" else (
                        "network_failure" if code == "source_network_error" else
                        "source_rejected" if code in {"source_http_401", "source_http_403", "source_http_429"} else
                        "request_or_code_failure" if code in {"source_http_400", "source_http_404", "source_http_422"} else
                        "source_service_failure" if code.startswith("source_http_") else "code_failure")
                    pending.remove(row)
            if time.monotonic() - last_progress >= 30:
                print("Completed real source checks:", len(report["sources"]) - len(pending), "/", len(report["sources"]), flush=True)
                last_progress = time.monotonic()
            time.sleep(2 if pending else 0)
        for row in pending:
            row["outcome"] = "not_finished_within_verification_window"
        snapshot = get("/api/v1/ops/freshness", True)
        report["freshness"] = snapshot
        expected = {adapter for adapter in SOURCE_DEFINITIONS if adapter != "offline"}
        check("all_registered_real_adapters_attempted", {row["adapter"] for row in report["sources"]} == expected
              and all(row.get("task_id") for row in report["sources"]))
        check("all_attempts_terminal", not pending)
        for row in report["sources"]:
            check("real_source_success_" + row["adapter"], row.get("status") == "succeeded")
        check("effective_intervals_within_4_hours_except_daily_cache", all(
            s["interval_seconds"] <= (86400 if s["adapter"] == "arxiv" else 14400)
            for s in snapshot["sources"] if s["enabled"]))
        check("arxiv_daily_interval", all(s["interval_seconds"] >= 86400 for s in snapshot["sources"] if s["adapter"] == "arxiv"))
        for basis in ["published", "updated", "discovered", "changed"]:
            data = get(f"/api/v1/documents?recent_hours=48&time_basis={basis}&ai_relevant=true&page_size=100")
            report["recent_" + basis] = data
            check("recent_" + basis + "_excludes_demo", all(not d["is_demo"] for d in data["items"]))
            attr = {"published": "published_at", "updated": "source_updated_at",
                    "discovered": "first_collected_at", "changed": "content_changed_at"}[basis]
            now = datetime.now(timezone.utc)
            check("recent_" + basis + "_timestamp_bounds", all(d[attr] and
                0 <= (now - datetime.fromisoformat(d[attr].replace("Z", "+00:00"))).total_seconds() <= 172810
                for d in data["items"]))
        check("intelligence_recent_filter", "items" in get("/api/v1/intelligence?time_basis=updated&recent_hours=48"))
        check("invalid_time_basis_rejected", client.get("/api/v1/documents?time_basis=other&recent_hours=48").status_code == 422)
        check("missing_timezone_rejected", client.get("/api/v1/documents?time_from=2026-10-01T00:00:00").status_code == 422)
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        report["limits"] = ["This controlled run does not prove uninterrupted 48h monitoring",
            "No new publications are guaranteed; no-change is a valid source outcome"]
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output), "checks_passed": sum(c["passed"] for c in report["checks"]),
        "checks_total": len(report["checks"]), "sources": [{"adapter": s["adapter"], "outcome": s.get("outcome"),
            "created": s.get("result", {}).get("created"), "updated": s.get("result", {}).get("updated"),
            "error_code": s.get("error_code")} for s in report["sources"]], "recent": report["freshness"]["recent"]}, ensure_ascii=False), flush=True)
    return 0 if all(c["passed"] for c in report["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
