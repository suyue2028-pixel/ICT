"""Fictional accounts in rollback-only DBs; no SMS or model calls."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import pytest
from fastapi import HTTPException
from redis import RedisError
from sqlalchemy import func, select
from test_agent_upgrade import db, client, token
from backend.core.config import get_settings
from backend.core.passwords import hash_password, verify_password, PasswordBusy
from backend.models import UserAccount, AccessToken, Conversation
from backend.services.accounts import issue_session, limit_attempts
from backend.services.jobs import utc_now

PASSWORD = "Fictional passphrase for tests!"


@pytest.fixture(autouse=True)
def isolated_attempts(monkeypatch):
    monkeypatch.setattr("backend.api.v1.auth.limit_attempts", lambda *args: None)


def register(client, phone="19900000001"):
    response = client.post("/api/v1/auth/register", json={"phone": phone, "password": PASSWORD})
    assert response.status_code == 201
    return response.json()


def headers(value):
    return {"Authorization": "Bearer " + value["access_token"]}


def test_registration_real_password_login_identity_logout_and_admin_isolation(db, client):
    first = register(client)
    account = db.scalar(select(UserAccount))
    assert account.phone == "+8619900000001" and not account.phone_verified
    assert account.password_hash != PASSWORD and verify_password(PASSWORD, account.password_hash)
    assert PASSWORD not in str(first) and account.password_hash not in str(first)
    assert first["phone_masked"] == "+86199****0001" and not first["phone_verified"]
    assert client.get("/api/v1/auth/me", headers=headers(first)).json()["owner_id"] == first["owner_id"]
    assert client.get("/api/v1/ops/enrichment", headers=headers(first)).status_code == 401
    assert client.post("/api/v1/auth/tokens", headers=headers(first), json={"owner_id": "admin"}).status_code == 401
    login = client.post("/api/v1/auth/login", json={"phone": "+86 199-0000-0001", "password": PASSWORD})
    assert login.status_code == 200 and login.headers["cache-control"] == "no-store"
    second = login.json()
    assert second["owner_id"] == first["owner_id"] and second["access_token"] != first["access_token"]
    stored = db.scalar(select(AccessToken).where(AccessToken.token_hash == hashlib.sha256(second["access_token"].encode()).hexdigest()))
    assert stored.account_id == account.id
    assert client.post("/api/v1/auth/logout", headers=headers(second)).json() == {"logged_out": True}
    assert client.get("/api/v1/auth/me", headers=headers(second)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=headers(first)).status_code == 200


def test_canonical_phone_duplicates_do_not_overwrite_accounts_or_issue_tokens(db, client):
    first = register(client)
    duplicate = client.post("/api/v1/auth/register", json={"phone": "+8619900000001", "password": "Another fictional passphrase!"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "registration_unavailable"
    assert db.scalar(select(func.count()).select_from(UserAccount)) == 1
    assert db.scalar(select(func.count()).select_from(AccessToken)) == 1
    assert client.get("/api/v1/auth/me", headers=headers(first)).status_code == 200


def test_accounts_cannot_read_other_users_conversations_and_legacy_tokens_remain_valid(db, client):
    first, second = register(client), register(client, "19900000002")
    conversation = Conversation(owner_id=first["owner_id"], provider="external", is_demo=False, turn_count=0)
    db.add(conversation); db.flush()
    path = f"/api/v1/agent/sessions/{conversation.id}"
    assert client.get(path, headers=headers(first)).status_code == 200
    assert client.get(path, headers=headers(second)).status_code == 403
    assert client.get("/api/v1/auth/me", headers=token(db, "legacy-owner")).json()["owner_id"] == "legacy-owner"


def test_disabled_expired_and_revoked_account_sessions_are_rejected(db, client):
    session = register(client)
    account = db.scalar(select(UserAccount)); account.disabled = True; db.flush()
    assert client.get("/api/v1/auth/me", headers=headers(session)).status_code == 401
    assert client.post("/api/v1/auth/login", json={"phone": "19900000001", "password": PASSWORD}).status_code == 401
    account.disabled = False
    row = db.scalar(select(AccessToken)); row.expires_at = utc_now() - timedelta(seconds=1); db.flush()
    assert client.get("/api/v1/auth/me", headers=headers(session)).status_code == 401


@pytest.mark.parametrize("phone,password", [("not-a-phone", PASSWORD), ("19900000001", "short"),
    ("19900000001", "123456789012345"), ("19900000001", "a" * 20), ("19900000001", "x" * 129)])
def test_invalid_registration_is_rejected_without_echoing_password(db, client, phone, password):
    response = client.post("/api/v1/auth/register", json={"phone": phone, "password": password})
    assert response.status_code == 422 and password not in response.text
    assert db.scalar(select(func.count()).select_from(UserAccount)) == 0


def test_login_errors_do_not_distinguish_unknown_phone_and_wrong_password(db, client):
    register(client)
    responses = [client.post("/api/v1/auth/login", json={"phone": phone, "password": "wrong test passphrase!"})
                 for phone in ("19900000001", "19900000009")]
    assert [row.status_code for row in responses] == [401, 401]
    assert {row.json()["error"]["code"] for row in responses} == {"invalid_credentials"}


def test_registration_switch_and_bounded_password_work(db, client, monkeypatch):
    settings = get_settings().model_copy(update={"signup_enabled": False})
    monkeypatch.setattr("backend.api.v1.auth.get_settings", lambda: settings)
    assert client.post("/api/v1/auth/register", json={"phone": "19900000001", "password": PASSWORD}).status_code == 403
    monkeypatch.setattr("backend.api.v1.auth.get_settings", get_settings)
    def busy(*args):
        raise PasswordBusy()
    monkeypatch.setattr("backend.services.accounts.hash_password", busy)
    response = client.post("/api/v1/auth/register", json={"phone": "19900000001", "password": PASSWORD})
    assert response.status_code == 429 and response.headers["retry-after"] == "2"


def test_salted_hash_unicode_normalization_and_untrusted_cost_parameters():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and verify_password(PASSWORD, first) and not verify_password("wrong", first)
    assert not verify_password(PASSWORD, first.replace("131072", "1073741824"))
    assert not verify_password(PASSWORD, "not-a-password-hash")
    original = "Fictional phrase with cafe\u0301"
    assert verify_password("Fictional phrase with café", hash_password(original))


def test_session_cap_revokes_only_older_sessions_of_same_account(db, client):
    register(client)
    account = db.scalar(select(UserAccount))
    for _ in range(11):
        issue_session(db, account)
    assert db.scalar(select(func.count()).select_from(AccessToken).where(AccessToken.account_id == account.id,
        AccessToken.revoked.is_(False))) == 10


def test_shared_redis_rate_limits_and_failure_are_enforced(monkeypatch):
    settings = get_settings().model_copy(update={"postgres_db": "verify_auth_rate_" + uuid4().hex})
    monkeypatch.setattr("backend.services.accounts.get_settings", lambda: settings)
    request = SimpleNamespace(client=SimpleNamespace(host="192.0.2.10"))
    for _ in range(3):
        limit_attempts(request, "+8619900000001", "register")
    with pytest.raises(HTTPException) as error:
        limit_attempts(request, "+8619900000001", "register")
    assert error.value.status_code == 429 and int(error.value.headers["Retry-After"]) > 0
    def fail(*args, **kwargs):
        raise RedisError()
    monkeypatch.setattr("backend.services.accounts.Redis.from_url", fail)
    with pytest.raises(HTTPException) as error:
        limit_attempts(request, "+8619900000002", "login")
    assert error.value.status_code == 503 and error.value.detail == "auth_throttle_unavailable"
