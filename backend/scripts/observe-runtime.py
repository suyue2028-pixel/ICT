"""Start/read the existing beat's persistent observation; never creates another scheduler."""
import argparse
import json
from pathlib import Path
import sys
import httpx
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "status", "samples"])
    parser.add_argument("--id")
    parser.add_argument("--hours", type=int, default=48)
    args = parser.parse_args()
    state = ROOT / "output/runtime-observation.json"
    settings = get_settings()
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", trust_env=False, timeout=20,
            headers={"X-API-Key": settings.admin_api_key.get_secret_value()}) as client:
        if args.command == "start":
            response = client.post("/api/v1/ops/observations", json={"hours": args.hours})
        else:
            identifier = args.id or json.loads(state.read_text(encoding="utf-8"))["observation_id"]
            response = client.get("/api/v1/ops/observations/" + identifier + ("/samples" if args.command == "samples" else ""))
        if response.status_code >= 400:
            raise SystemExit(f"Observation HTTP {response.status_code}; no sensitive response printed")
        data = response.json()
    if args.command != "samples":
        state.parent.mkdir(exist_ok=True)
        state.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data if args.command == "start" else {k:v for k,v in data.items() if k not in {"latest", "items"}},
        ensure_ascii=False, indent=2))
    if args.command == "samples":
        path = ROOT / "output/runtime-observation-samples-page1.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("First page saved to " + str(path))


if __name__ == "__main__":
    main()
