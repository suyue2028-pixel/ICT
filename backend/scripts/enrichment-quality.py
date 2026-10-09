"""Admin quality CLI; credentials remain in memory, review files are private output."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings


def save_private(data, name):
    folder = ROOT / "output/enrichment-quality"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["summary", "issues", "create", "export", "submit", "report"])
    parser.add_argument("--batch-id")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--per-source", type=int, default=12)
    parser.add_argument("--document-type", choices=["vulnerability", "advisory", "all"], default="vulnerability")
    parser.add_argument("--prediction-method", choices=["source_reported", "external", "all"], default="source_reported")
    args = parser.parse_args()
    if args.action in {"export", "submit", "report"} and not args.batch_id:
        parser.error("--batch-id is required")
    if args.action == "submit" and not args.file:
        parser.error("--file is required: one completed review_template JSON object")
    settings = get_settings()
    prefix = f"http://127.0.0.1:{settings.api_port}/api/v1/ops/enrichment-quality"
    params = {"document_type": args.document_type, "prediction_method": args.prediction_method}
    with httpx.Client(timeout=60, trust_env=False, headers={"X-API-Key": settings.admin_api_key.get_secret_value()}) as client:
        if args.action == "create":
            response = client.post(prefix + "/batches", json={**params, "per_source": args.per_source})
        elif args.action == "submit":
            response = client.post(prefix + f"/batches/{args.batch_id}/reviews", json=json.loads(args.file.read_text(encoding="utf-8-sig")))
        else:
            suffix = {"summary": "", "issues": "/issues", "export": f"/batches/{args.batch_id}",
                      "report": f"/batches/{args.batch_id}/report"}[args.action]
            response = client.get(prefix + suffix, params=params if args.action in {"summary", "issues"} else None)
        if response.status_code >= 400:
            print(json.dumps({"status": response.status_code, "error": response.json().get("error", {}).get("code", "request_failed")}))
            return 1
        data = response.json()
    path = save_private(data, args.action)
    if args.action == "export":
        result = {"path": path, "batch_id": data["batch_id"], "cases": len(data["cases"]), "gold_labels_filled": False}
    else:
        result = {"path": path, **data}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
