import secrets
import hashlib
from typing import Annotated

from fastapi import HTTPException, Security, Depends
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from backend.api.dependencies import Database
from backend.models import AccessToken

from backend.core.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
tool_header = APIKeyHeader(name="X-Agent-Token", auto_error=False)
bearer = HTTPBearer(auto_error=False)


def require_admin(api_key: str | None = Security(api_key_header)) -> None:
    expected = get_settings().admin_api_key.get_secret_value()
    if not api_key or not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _internal(api_key, token, setting):
    s = get_settings()
    expected = getattr(s, setting, None)
    if token and expected and secrets.compare_digest(token.encode(), expected.get_secret_value().encode()):
        return
    require_admin(api_key)


def require_tool(api_key: str | None = Security(api_key_header), token: str | None = Security(tool_header)):
    _internal(api_key, token, "agent_tool_token")


def require_internal_write(api_key: str | None = Security(api_key_header), token: str | None = Security(tool_header)):
    _internal(api_key, token, "agent_write_token")


def require_user(db: Database, credential: HTTPAuthorizationCredentials | None = Security(bearer)) -> str:
    from datetime import datetime, timezone
    if credential is None or len(credential.credentials) > 256:
        raise HTTPException(401, "user_token_required")
    row = db.scalar(select(AccessToken).where(
        AccessToken.token_hash == hashlib.sha256(credential.credentials.encode()).hexdigest(),
        AccessToken.revoked.is_(False), AccessToken.expires_at > datetime.now(timezone.utc)))
    if row is None:
        raise HTTPException(401, "invalid_user_token")
    return row.owner_id


User = Annotated[str, Depends(require_user)]
