"""Administrator issues expiring, revocable user tokens; database stores hashes only."""
import hashlib
import secrets
from datetime import timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, Response
from backend.api.dependencies import Database, require_record
from backend.core.security import require_admin, User
from backend.models import AccessToken
from backend.schemas.agent import TokenCreate
from backend.services.jobs import utc_now

router = APIRouter(prefix="/auth", tags=["用户权限"])


@router.get("/me")
def current_user(owner: User, response: Response):
    """Validate the existing user credential; no token issuance or model secret."""
    response.headers["Cache-Control"] = "no-store"
    return {"owner_id": owner, "authenticated": True}


@router.post("/tokens", status_code=201, dependencies=[Depends(require_admin)])
def create_token(body: TokenCreate, db: Database, response: Response):
    secret = secrets.token_urlsafe(48)
    row = AccessToken(owner_id=body.owner_id, token_hash=hashlib.sha256(secret.encode()).hexdigest(),
                      expires_at=utc_now() + timedelta(hours=body.expires_in_hours), revoked=False)
    db.add(row)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"id": row.id, "owner_id": row.owner_id, "expires_at": row.expires_at,
            "access_token": secret, "token_type": "Bearer", "note": "One-time display; never share admin key"}


@router.delete("/tokens/{token_id}", dependencies=[Depends(require_admin)])
def revoke_token(token_id: UUID, db: Database):
    row = require_record(db, AccessToken, token_id)
    row.revoked = True
    db.commit()
    return {"id": row.id, "revoked": True}
