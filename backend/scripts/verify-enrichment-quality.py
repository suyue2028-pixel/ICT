"""Bounded live verification; creates only frozen real review data, never gold labels."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", help="Reuse an existing frozen sample without creating another")
    args = parser.parse_args()
    settings = get_settings()
    base = f"http://127.0.0.1:{settings.api_port}"
    prefix = base + "/api/v1/ops/enrichment-quality"
    headers = {"X-API-Key": settings.admin_api_key.get_secret_value()}
    checks = {}
    with httpx.Client(timeout=30, trust_env=False) as client:
        checks["api_ready"] = client.get(base + "/health/ready").status_code == 200
        checks["summary_requires_admin"] = client.get(prefix).status_code == 401
        checks["batch_creation_requires_admin"] = client.post(prefix + "/batches", json={}).status_code == 401
        r = client.get(prefix, headers=headers)
        r.raise_for_status()
        summary = r.json()
        checks["six_quality_dimensions"] = len(summary["dimensions"]) == 6
        checks["real_current_scope"] = summary["scope"]["is_demo"] is False and summary["scope"]["document_type"] == "vulnerability"
        checks["complete_bounded_scan"] = summary["scan_complete"]
        issues = client.get(prefix + "/issues", params={"page_size": 100}, headers=headers).json()
        checks["issue_pagination"] = len(issues["items"]) <= 100 and issues["total"] >= len(issues["items"])
        batch_id = args.batch_id
        if not batch_id:
            r = client.post(prefix + "/batches", headers=headers, json={"per_source": 24, "max_cases": 100,
                "seed": "real-quality-20261009", "is_demo": False, "document_type": "vulnerability"})
            r.raise_for_status()
            batch_id = r.json()["batch_id"]
        exported = client.get(prefix + f"/batches/{batch_id}", headers=headers).json()
        report = client.get(prefix + f"/batches/{batch_id}/report", headers=headers).json()
        checks["frozen_samples_have_versions"] = bool(exported["cases"]) and all(
            len(case["content_hash"]) == 64 and len(case["prediction_hash"]) == 64 for case in exported["cases"])
        checks["no_fabricated_human_labels"] = all(field["expected"] is None
            for case in exported["cases"] for field in case["review_template"]["field_reviews"].values())
        checks["no_score_from_unreviewed_data"] = (report["submitted_review_documents"] != 0 or
            report["overall"]["precision"] is None and report["overall"]["recall"] is None)
        checks["review_material_requires_admin"] = client.get(prefix + f"/batches/{batch_id}").status_code == 401
        checks["schema_published"] = "/api/v1/ops/enrichment-quality" in client.get(base + "/openapi.json").json()["paths"]
    docker = Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
    source_hashes = {}
    for relative in ["backend/services/enrichment_quality.py", "backend/api/v1/quality.py", "backend/services/observation.py"]:
        local_hash = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        result = subprocess.run([str(docker), "exec", "cit-backend-development-api-1", "python", "-c",
            "from pathlib import Path; import hashlib; print(hashlib.sha256(Path(" + repr("/app/" + relative) + ").read_bytes()).hexdigest())"],
            capture_output=True, text=True, check=True)
        source_hashes[relative] = local_hash == result.stdout.strip()
    checks["running_code_matches_source"] = all(source_hashes.values())
    beat = subprocess.run([str(docker), "ps", "--filter", "label=com.docker.compose.project=cit-backend-development",
        "--filter", "label=com.docker.compose.service=beat", "--format", "{{.ID}}"], capture_output=True, text=True, check=True)
    checks["exactly_one_beat"] = len(beat.stdout.split()) == 1
    with httpx.Client(timeout=30, trust_env=False, headers=headers) as client:
        observation = client.get(base + "/api/v1/ops/observations/bfb1b544-95e8-4011-b4ba-2ef3790da55e").json()
    latest = observation.get("latest", {})
    checks["quality_sample_automatically_persisted"] = "enrichment_quality" in latest
    folder = ROOT / "output/enrichment-quality"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    sample_path = folder / ("real-review-" + stamp + ".json")
    with sample_path.open("x", encoding="utf-8") as handle:
        json.dump(exported, handle, ensure_ascii=False, indent=2)
    result = {"time": datetime.now(timezone.utc).isoformat(), "checks": checks, "passed": all(checks.values()),
        "batch_id": batch_id, "sample_documents": len(exported["cases"]), "review_file": str(sample_path),
        "source_counts": exported["selection"]["source_counts"], "monitoring": summary,
        "reviewed_evaluation": report, "source_hashes_match": source_hashes,
        "model_calls": 0, "production_documents_modified": False,
        "observation_status": observation.get("status"), "observation_gap_seconds": observation.get("gap_seconds")}
    path = folder / ("runtime-" + stamp + ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"path": str(path), "passed": result["passed"], "checks": checks,
        "batch_id": batch_id, "sample_documents": result["sample_documents"], "review_file": str(sample_path),
        "automatic_checks": summary["automatic_checks"], "model_calls": 0}, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
