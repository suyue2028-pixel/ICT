"""Admin-local source coverage snapshot; optional activation of the two new sources."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.collectors.registry import SOURCE_DEFINITIONS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable-discovery", action="store_true", help="Enable only newly registered discovery sources")
    parser.add_argument("--wait-catalogue", type=int, default=0, help="Bounded wait seconds for NVIDIA catalogue tail")
    args = parser.parse_args()
    if not 0 <= args.wait_catalogue <= 1200:
        parser.error("--wait-catalogue must be between 0 and 1200")
    settings = get_settings()
    headers = {"X-API-Key": settings.admin_api_key.get_secret_value()}
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", timeout=30, trust_env=False) as client:
        def get(path, admin=True):
            response = client.get(path, headers=headers if admin else {})
            response.raise_for_status()
            return response.json()
        if args.enable_discovery:
            for source in get("/api/v1/sources", False):
                if source["adapter"] in {"nist_drafts", "govuk_search"} and not source["enabled"]:
                    response = client.patch("/api/v1/sources/" + source["id"], headers=headers,
                        json={"enabled": True})
                    response.raise_for_status()
                    print(json.dumps({"enabled_adapter": source["adapter"]}), flush=True)
        deadline = time.monotonic() + args.wait_catalogue
        while True:
            snapshot = get("/api/v1/ops/coverage")
            nvidia = next((source for source in snapshot["sources"] if source["adapter"] == "nvidia"), {})
            catalogue = nvidia.get("catalogue_capture")
            if not args.wait_catalogue or catalogue and catalogue["not_stored"] == 0 or time.monotonic() >= deadline:
                break
            print(json.dumps({"catalogue_waiting": True, "captured": catalogue}), flush=True)
            time.sleep(min(30, max(0, deadline - time.monotonic())))
        expected = {adapter for adapter in SOURCE_DEFINITIONS if adapter != "offline"}
        checks = {"api_ready": get("/health/ready", False)["status"] == "ready",
            "coverage_requires_admin": client.get("/api/v1/ops/coverage").status_code == 401,
            "registered_adapters_visible": expected <= {source["adapter"] for source in snapshot["sources"]},
            "seven_categories_with_current_rule_ai_documents": len(snapshot["categories"]["with_current_ai_documents"]) >= 7,
            "no_false_sla_or_recall": not snapshot["continuous_sla_verified"] and snapshot["independent_vulnerability_recall"] is None,
            "enabled_intervals_within_48h": all(source["interval_seconds"] <= 172800 for source in snapshot["sources"] if source["enabled"]),
            "new_discovery_sources_really_succeeded": all(any(source["adapter"] == adapter and source["enabled"] and
                source["last_success_at"] for source in snapshot["sources"]) for adapter in {"nist_drafts", "govuk_search"}),
            "nvidia_observed_catalogue_captured": bool(catalogue) and catalogue["not_stored"] == 0}
        report = {"measured_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
            "passed": all(checks.values()), "snapshot": snapshot, "model_calls": 0,
            "note": "Saved catalogue capture and rule-based source coverage are not independent recall or continuous SLA"}
    folder = ROOT / "output/coverage"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("runtime-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"report": str(path), "checks": checks, "counts": snapshot["adapter_counts"],
        "nvidia_catalogue": catalogue, "delivery": snapshot["delivery"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
