"""Normal-user account sessions, bounded password work and shared attempt limits."""
import hashlib
import hmac
import secrets
from datetime import timedelta
from fastapi import HTTPException
from redis import Redis, RedisError
from sqlalchemy import select
from backend.core.config import get_settings
from backend.core.passwords import (MIN_LENGTH, MAX_LENGTH, DUMMY_HASH, PasswordBusy,
    hash_password, normalize_password, verify_password)
from backend.models import AccessToken
from backend.services.jobs import utc_now

_LIMIT = """
local wait=0
for i,key in ipairs(KEYS) do
 local n=redis.call('INCR',key)
 if n==1 then redis.call('EXPIRE',key,tonumber(ARGV[i*2])) end
 if n>tonumber(ARGV[i*2-1]) then wait=math.max(wait,redis.call('TTL',key)) end
end
return wait
"""


def limit_attempts(request, phone, action):
    settings = get_settings()
    # Forwarded headers are not trusted: only the actual peer is considered.
    peer = request.client.host if request.client else "unknown"
    secret = settings.admin_api_key.get_secret_value().encode()
    digest = lambda value: hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()
    prefix = "ict:accounts:" + settings.postgres_db + ":" + action + ":"
    limits = [5, 3600, 3, 3600] if action == "register" else [30, 60, 10, 60]
    try:
        with Redis.from_url(settings.redis_url(2), socket_connect_timeout=1, socket_timeout=1) as redis:
            wait = redis.eval(_LIMIT, 2, prefix + "peer:" + digest(peer), prefix + "phone:" + digest(phone), *limits)
    except RedisError:
        raise HTTPException(503, "auth_throttle_unavailable") from None
    if wait > 0:
        raise HTTPException(429, "auth_rate_limited", headers={"Retry-After": str(max(1, wait))})


def validate_new_password(value, phone):
    value = normalize_password(value)
    common = {"password123456789", "123456789012345", "1234567890123456", "qwertyuiopasdfgh", "correcthorsebatterystaple"}
    if (not MIN_LENGTH <= len(value) <= MAX_LENGTH or len(value.encode()) > 512
            or len(set(value)) < 3 or value.casefold() in common or phone[3:] in value):
        raise HTTPException(422, "weak_password")
    return value


def checked_hash(password):
    try:
        return hash_password(password)
    except PasswordBusy:
        raise HTTPException(429, "password_work_busy", headers={"Retry-After": "2"}) from None


def checked_verify(password, encoded):
    try:
        return verify_password(password, encoded or DUMMY_HASH)
    except PasswordBusy:
        raise HTTPException(429, "password_work_busy", headers={"Retry-After": "2"}) from None


def account_owner(account):
    return "account:" + str(account.id)


def issue_session(db, account):
    # Caller locks this account first, serializing the active-session cap.
    existing = db.scalars(select(AccessToken).where(AccessToken.account_id == account.id,
        AccessToken.revoked.is_(False), AccessToken.expires_at > utc_now()).order_by(AccessToken.created_at.desc(), AccessToken.id)).all()
    for row in existing[9:]:
        row.revoked = True
    value = secrets.token_urlsafe(48)
    row = AccessToken(account_id=account.id, owner_id=account_owner(account),
        token_hash=hashlib.sha256(value.encode()).hexdigest(), revoked=False,
        expires_at=utc_now() + timedelta(hours=get_settings().account_session_hours))
    db.add(row); db.flush()
    return {"access_token": value, "token_type": "Bearer", "expires_at": row.expires_at,
        "owner_id": row.owner_id, "account_id": account.id,
        "phone_masked": account.phone[:6] + "****" + account.phone[-4:], "phone_verified": account.phone_verified}
