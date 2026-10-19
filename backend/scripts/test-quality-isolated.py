"""Create a NEW test database; all fixture rows roll back; retain the DB for inspection."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = "verify_quality_" + stamp.lower() + "_" + uuid4().hex[:8]
    settings = get_settings()
    admin = create_engine(settings.database_url.set(database="postgres"), hide_parameters=True,
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + name + '"'))
    admin.dispose()
    os.environ["POSTGRES_DB"] = name
    get_settings.cache_clear()
    from alembic import command
    from alembic.config import Config
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    command.check(config)
    import pytest
    code = pytest.main([str(ROOT / "tests"), "-q", "-p", "no:cacheprovider", "--tb=short"])
    folder = ROOT / "output/enrichment-quality"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("isolated-tests-" + stamp + ".json")
    path.write_text(json.dumps({"database": name, "test_exit_code": code, "production_data_modified": False,
        "migration": "0008_enrichment_quality", "retained_for_inspection": True}, indent=2), encoding="utf-8")
    print(json.dumps({"result": str(path), "test_exit_code": code, "isolated_database": name}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
