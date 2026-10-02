from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import text

from backend.core.config import get_settings, PROJECT_ROOT
from backend.db.session import get_engine

router = APIRouter(prefix="/health", tags=["运行状态"])


@router.get("/live")
def live():
    return {"status": "ok", "project": "后端开发", "scope": "development-framework"}


@router.get("/ready")
def ready():
    checks = {"postgres": False, "redis": False, "migration": False}
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
            checks["postgres"] = True
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            config = Config()
            config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
            checks["migration"] = connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        pass
    try:
        with Redis.from_url(get_settings().redis_url(), socket_connect_timeout=2, socket_timeout=2) as client:
            checks["redis"] = bool(client.ping())
    except Exception:
        pass
    healthy = all(checks.values())
    return JSONResponse({"status": "ready" if healthy else "unavailable", "checks": checks},
                        status_code=200 if healthy else 503)
