"""Read-only live verification of the shared 4h target and complete quality scan."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter

import httpx
from sqlalchemy import func, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import Document, EnrichmentQualityReview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", action="store_true", help="Save the current baseline without requiring the new code")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--compose-project", default=os.environ.get("COMPOSE_PROJECT_NAME", "cit-backend-development"))
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    settings = get_settings()
    headers = {"X-API-Key": settings.admin_api_key.get_secret_value()}
    checks, timings, snapshots = {}, {}, {}
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", headers=headers,
                      timeout=60, trust_env=False) as client:
        checks["api_ready"] = client.get("/health/ready").status_code == 200
        checks["agent_healthy"] = client.get("/api/v1/agent/health").status_code == 200
        for name, path in (("freshness", "/api/v1/ops/freshness"), ("coverage", "/api/v1/ops/coverage"),
                           ("quality", "/api/v1/ops/enrichment-quality")):
            checks[name + "_requires_admin"] = client.get(path, headers={"X-API-Key": "invalid"}).status_code == 401
            started = perf_counter()
            response = client.get(path)
            response.raise_for_status()
            timings[name + "_seconds"] = round(perf_counter() - started, 4)
            snapshots[name] = response.json()
        fresh, coverage, quality = (snapshots[name] for name in ("freshness", "coverage", "quality"))
        checks["target_is_four_hours"] = fresh["schedule_policy"]["target_seconds"] == 14400
        checks["no_false_sla_claim"] = not coverage["continuous_sla_verified"] and not fresh["schedule_policy"]["delivery_target_verified"]
        if not args.before:
            targets = [item["target_seconds"] for item in coverage["delivery"].values()
                       if isinstance(item, dict) and "target_seconds" in item]
            for source in coverage["sources"]:
                targets.extend(item["target_seconds"] for item in source["delivery"].values()
                               if isinstance(item, dict) and "target_seconds" in item)
                targets.append(source["queue_and_retry_wait"]["target_seconds"])
            targets.extend((fresh["publish_to_first_collection_seconds"]["recent_publications"]["target_seconds"],
                            fresh["source_update_to_changed_version_seconds"]["target_seconds"]))
            checks["all_latency_targets_match"] = bool(targets) and all(value == 14400 for value in targets)
            checks["all_current_documents_scanned"] = quality["scan_complete"] and quality["total_documents"] == quality["scanned_documents"]
            checks["unlimited_scope_with_bounded_fetch"] = quality["scan_limit"] is None and quality["scan_batch_size"] == 200
            checks["one_snapshot"] = quality["scan_consistency"] == "single_statement_snapshot"
            checks["six_dimension_denominators_match"] = len(quality["dimensions"]) == 6 and all(
                sum(count for state, count in values.items() if state != "known_ratio") == quality["scanned_documents"]
                for values in quality["dimensions"].values())
            checks["automatic_checks_not_claimed_as_accuracy"] = (quality["accuracy"] is None and quality["recall"] is None
                and quality["automatic_checks"]["semantic_accuracy"] is None)
            response = client.get("/api/v1/ops/enrichment-quality", params={"batch_size": 37})
            response.raise_for_status()
            alternate = response.json()
            checks["different_batch_size_same_result"] = all(quality[key] == alternate[key] for key in
                ("total_documents", "scanned_documents", "dimensions", "automatic_checks"))
            response = client.get("/api/v1/ops/enrichment-quality/issues", params={"page_size": 3, "page": 1})
            response.raise_for_status()
            issues = response.json()
            checks["issues_paginated_over_complete_scope"] = (issues["scan_complete"] and
                issues["scanned_documents"] == quality["scanned_documents"] and len(issues["items"]) <= 3 and
                issues["total"] == quality["automatic_checks"]["error_count"] + quality["automatic_checks"]["warning_count"])
            snapshots["issue_pagination"] = {key: issues[key] for key in
                ("total", "scanned_documents", "total_documents", "page", "page_size", "scan_complete")}
            spec = client.get("/openapi.json").json()["paths"]
            checks["published_contract_current"] = "4小时" in spec["/api/v1/ops/coverage"]["get"]["description"] and any(
                value["name"] == "batch_size" for value in spec["/api/v1/ops/enrichment-quality"]["get"]["parameters"])
    with get_session_factory()() as db:
        documents = db.scalar(select(func.count()).select_from(Document))
        scope_documents = db.scalar(select(func.count()).select_from(Document).where(
            Document.is_demo.is_(False), Document.ai_relevant.is_(True), Document.deleted_at.is_(None),
            Document.document_type == "vulnerability"))
        review_count = db.scalar(select(func.count()).select_from(EnrichmentQualityReview))
        migration = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if not args.before:
        checks["independent_scope_count_matches"] = scope_documents == quality["total_documents"]
        if baseline:
            checks["document_count_not_reduced"] = documents >= baseline["documents"]
            checks["review_count_unchanged"] = review_count == baseline["review_count"]
        docker = shutil.which("docker") or str(Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe")
        def command(*values):
            return subprocess.run([docker, *values], capture_output=True, text=True, check=True).stdout.strip()
        relatives = ["backend/services/coverage.py", "backend/services/freshness.py", "backend/services/enrichment_quality.py",
                     "backend/services/observation.py", "backend/api/v1/quality.py", "backend/api/v1/operations.py"]
        expected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in relatives}
        code = "import hashlib,json; from pathlib import Path; print(json.dumps({p:hashlib.sha256(Path('/app',p).read_bytes()).hexdigest() for p in " + repr(relatives) + "}))"
        hashes = {service: json.loads(command("exec", args.compose_project + "-" + service + "-1", "python", "-c", code)) == expected
                  for service in ("api", "worker", "beat")}
        checks["deployed_source_matches"] = all(hashes.values())
        beats = command("ps", "--filter", "label=com.docker.compose.project=" + args.compose_project,
                        "--filter", "label=com.docker.compose.service=beat", "--format", "{{.ID}}")
        checks["one_project_beat"] = len(beats.split()) == 1
        snapshots["runtime_source_hashes_match"] = hashes
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "phase": "before" if args.before else "after",
        "checks": checks, "passed": all(checks.values()), "request_timings": timings, "documents": documents,
        "scope_documents": scope_documents, "review_count": review_count, "migration": migration,
        "snapshots": snapshots, "model_calls": 0, "business_data_modified_by_verifier": False,
        "limitations": ["A full structural scan is not a human accuracy/recall evaluation",
                       "A live policy check is not proof of continuous four-hour delivery"]}
    folder = ROOT / "output/enrichment-quality"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("freshness-quality-" + report["phase"] + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "passed": report["passed"], "checks": checks,
        "scope_documents": scope_documents, "scanned_documents": quality["scanned_documents"],
        "scan_complete": quality["scan_complete"], "automatic_checks": quality["automatic_checks"],
        "timings": timings, "model_calls": 0}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        raise SystemExit("Read-only verification failed: " + type(error).__name__ + "; no secrets printed") from None
