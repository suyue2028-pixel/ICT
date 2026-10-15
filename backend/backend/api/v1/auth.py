"""Administrator issues expiring, revocable user tokens; database stores hashes only."""
import hashlib
import secrets
from datetime import timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, Response, Request, HTTPException, Security
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from backend.api.dependencies import Database, require_record
from backend.core.security import require_admin, User, bearer
from backend.core.config import get_settings
from backend.models import AccessToken, UserAccount
from backend.schemas.accounts import AccountCredentials, AccountSession
from backend.services.accounts import (limit_attempts, validate_new_password, checked_hash,
    checked_verify, issue_session)
from backend.schemas.agent import TokenCreate
from backend.services.jobs import utc_now

router = APIRouter(prefix="/auth", tags=["用户权限"])


@router.get("/me")
def current_user(owner: User, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"owner_id": owner, "authenticated": True}


@router.post("/register", status_code=201, response_model=AccountSession)
def register_account(body: AccountCredentials, request: Request, db: Database, response: Response):
    if not get_settings().signup_enabled:
        raise HTTPException(403, "registration_closed")
    limit_attempts(request, body.phone, "register")
    password = validate_new_password(body.password.get_secret_value(), body.phone)
    account = UserAccount(phone=body.phone, password_hash=checked_hash(password), phone_verified=False, disabled=False)
    try:
        db.add(account); db.flush()
        result = issue_session(db, account)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "registration_unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/login", response_model=AccountSession)
def login_account(body: AccountCredentials, request: Request, db: Database, response: Response):
    limit_attempts(request, body.phone, "login")
    account = db.scalar(select(UserAccount).where(UserAccount.phone == body.phone))
    digest = account.password_hash if account else None
    valid = checked_verify(body.password.get_secret_value(), digest)
    if not valid or account is None or account.disabled:
        raise HTTPException(401, "invalid_credentials")
    account = db.scalar(select(UserAccount).where(UserAccount.id == account.id).with_for_update().execution_options(populate_existing=True))
    if account is None or account.disabled or account.password_hash != digest:
        raise HTTPException(401, "invalid_credentials")
    result = issue_session(db, account)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/logout")
def logout_account(owner: User, db: Database, response: Response, credential=Security(bearer)):
    row = db.scalar(select(AccessToken).where(AccessToken.owner_id == owner,
        AccessToken.token_hash == hashlib.sha256(credential.credentials.encode()).hexdigest()).with_for_update())
    row.revoked = True
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"logged_out": True}


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
