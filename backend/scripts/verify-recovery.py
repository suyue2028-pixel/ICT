"""Restore a consistent snapshot into a NEW named database; retain both dump and clone."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_engine
from backend.db.base import Base
import backend.models


def check_application(database_name):
    """Use an isolated application dependency to read ONLY the verification DB."""
    if not re.fullmatch(r"verify_restore_[a-z0-9_]+", database_name):
        raise ValueError("Only a named verification database may be checked")
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from backend.db.session import get_db
    from backend.main import app
    engine = create_engine(get_settings().database_url.set(database=database_name), hide_parameters=True)
    factory = sessionmaker(engine)
    def restored_db():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = restored_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/documents?page_size=1")
            with engine.connect() as connection:
                expected = connection.scalar(text("SELECT count(*) FROM documents WHERE NOT is_demo AND deleted_at IS NULL"))
            return {"api_list_status": response.status_code, "expected_real_documents": expected,
                "restored_api_total": response.json().get("total"),
                "passed": response.status_code == 200 and response.json().get("total") == expected}
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def main():
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = "verify_restore_" + stamp.lower() + "_" + uuid4().hex[:8]
    assert re.fullmatch(r"verify_restore_[a-z0-9_]+", name)
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    dump = output / ("recovery-" + stamp + ".dump")
    log_path = output / ("recovery-" + stamp + ".log")
    docker = Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
    prefix = [str(docker), "exec", "cit-backend-development-postgres-1"]
    with log_path.open("w", encoding="utf-8") as log:
        # pg_dump and counts share a single repeatable-read snapshot, so active
        # collectors may continue without making row-count comparisons racy.
        with get_engine().connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin():
            if connection.scalar(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}):
                raise RuntimeError("Verification database unexpectedly exists; nothing overwritten")
            snapshot = connection.scalar(text("SELECT pg_export_snapshot()"))
            counts = {table: connection.scalar(text('SELECT count(*) FROM "' + table + '"')) for table in sorted(Base.metadata.tables)}
            version = connection.scalar(text("SELECT version_num FROM alembic_version"))
            with dump.open("xb") as handle:
                subprocess.run([*prefix, "pg_dump", "-U", settings.postgres_user, "-d", settings.postgres_db,
                    "-Fc", "--snapshot=" + snapshot], stdout=handle, stderr=log, check=True)
        subprocess.run([*prefix, "createdb", "-U", settings.postgres_user, "--maintenance-db=" + settings.postgres_db,
            "--template=template0", name], stdout=log, stderr=log, check=True)
        with dump.open("rb") as handle:
            subprocess.run([str(docker), "exec", "-i", "cit-backend-development-postgres-1", "pg_restore",
                "-U", settings.postgres_user, "-d", name, "--no-owner", "--no-privileges", "--exit-on-error"],
                stdin=handle, stdout=log, stderr=log, check=True)
    restored = create_engine(settings.database_url.set(database=name), hide_parameters=True)
    with restored.connect() as connection:
        actual = {table: connection.scalar(text('SELECT count(*) FROM "' + table + '"')) for table in counts}
        restored_version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        query_ok = connection.scalar(text("SELECT 1")) == 1
    restored.dispose()
    application = check_application(name)
    passed = counts == actual and version == restored_version and query_ok and application["passed"]
    result = {"time": datetime.now(timezone.utc).isoformat(), "passed": passed, "snapshot_consistent": True,
        "verification_database": name, "source_database_modified": False, "database_retained": True,
        "backup": str(dump), "log": str(log_path), "migration_version": version,
        "restored_migration_version": restored_version, "source_snapshot_counts": counts, "restored_counts": actual,
        "restored_application": application,
        "limits": "Schema/data and API listing restored in a separate database; complete host/credential disaster recovery remains separate."}
    (output / ("recovery-" + stamp + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in {"source_snapshot_counts", "restored_counts"}}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--check-application":
            report_path = Path(sys.argv[2]).resolve()
            if report_path.parent != (ROOT / "output").resolve():
                raise ValueError("Only this project's local recovery report may be amended")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["restored_application"] = check_application(report["verification_database"])
            report["passed"] = report["passed"] and report["restored_application"]["passed"]
            report["limits"] = "Schema/data and API listing restored in a separate database; complete host/credential disaster recovery remains separate."
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report["restored_application"], ensure_ascii=False))
        else:
            raise SystemExit(main())
    except Exception as error:
        raise SystemExit("Isolated recovery failed: " + type(error).__name__ + "; inspect private output log") from None
