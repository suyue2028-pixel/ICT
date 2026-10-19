"""Run repeatable backend gates ONLY in a dedicated database. Never use production."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-contracts", action="store_true")
    parser.add_argument("--new-db", action="store_true", help="Create and retain a NEW verify_ci_* DB for local gates")
    args = parser.parse_args()
    if args.new_db:
        from backend.core.config import get_settings
        from sqlalchemy import create_engine, text
        name = "verify_ci_" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower() + "_" + uuid4().hex[:8]
        admin = create_engine(get_settings().database_url.set(database="postgres"), hide_parameters=True,
                              isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text('CREATE DATABASE "' + name + '"'))
        admin.dispose()
        os.environ["POSTGRES_DB"] = name
        get_settings.cache_clear()
    name = os.environ.get("POSTGRES_DB", "")
    if not re.fullmatch(r"(?:ci_backend|verify_[a-z0-9_]+)", name):
        raise RuntimeError("Set POSTGRES_DB to ci_backend or a new verify_* database; production is forbidden")
    os.chdir(ROOT)
    from backend.core.config import get_settings
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    settings = get_settings()
    if settings.postgres_db != name:
        raise RuntimeError("Isolated database configuration mismatch")
    from redis import Redis
    with Redis.from_url(settings.redis_url(), socket_timeout=3) as redis:
        assert redis.ping()  # Read-only; do not flush the production/shared broker.
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    command.check(config)
    schema_paths = [*sorted((ROOT / "docs/contracts").glob("*.json")), ROOT / "docs/openapi.json"]
    before = {str(p): json.loads(p.read_text(encoding="utf-8")) for p in schema_paths}
    subprocess.run([sys.executable, "scripts/export-contracts.py"], check=True)
    drift = [Path(p).name for p, value in before.items()
             if json.loads(Path(p).read_text(encoding="utf-8")) != value]
    if args.check_contracts and drift:
        raise RuntimeError("Committed contract schema drift: " + ", ".join(drift))
    import yaml
    workflow_path = next((p for p in (ROOT / ".github/workflows/backend-ci.yml",
        ROOT.parent / ".github/workflows/backend-ci.yml") if p.is_file()), None)
    if workflow_path is None:
        raise RuntimeError("Backend CI workflow must be in the project or repository root")
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"} and "pull_request_target" not in workflow["on"]
    assert all(re.fullmatch(r"[^@]+@[a-f0-9]{40}", step["uses"]) for step in workflow["jobs"]["test"]["steps"] if "uses" in step)
    for folder in ("backend", "migrations", "scripts", "tests"):
        # Compile source without writing bytecode or importing optional services.
        for path in (ROOT / folder).rglob("*.py"):
            compile(path.read_bytes(), str(path), "exec")
    subprocess.run([sys.executable, "scripts/check-public-files.py"], check=True)
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
    output = ROOT / "output/ci"
    output.mkdir(parents=True, exist_ok=True)
    # A fresh workspace-local path avoids shared Windows TEMP ownership issues.
    test_temp = output / ("pytest-" + uuid4().hex)
    code = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
                            "--basetemp=" + str(test_temp),
                            "--junitxml=" + str(output / "pytest.xml")])
    report = {"isolated_database": name, "test_exit_code": code, "schema_drift": drift,
        "migration_checked": True, "redis_ping": True, "api_ready": True,
        "external_model_calls": 0, "production_data_modified": False,
        "code_fingerprint": hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "backend").rglob("*.py")))).hexdigest()}
    (output / "local-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"test_exit_code": code, "report": "output/ci/local-result.json"}))
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except Exception as error:
        raise SystemExit("CI gate failed: " + type(error).__name__ + "; inspect the failed gate, no credentials printed") from None
