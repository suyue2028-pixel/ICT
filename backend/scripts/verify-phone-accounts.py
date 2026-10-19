"""Real HTTP verification with fictional accounts in a NEW dedicated database."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, select, text
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import UserAccount, Conversation, AccessToken


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    name = "verify_accounts_" + stamp + "_" + uuid4().hex[:8]
    original = get_settings()
    admin = create_engine(original.database_url.set(database="postgres"), hide_parameters=True, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + name + '"'))
    admin.dispose()
    os.environ["POSTGRES_DB"] = name
    get_settings.cache_clear()
    assert get_settings().postgres_db == name and name.startswith("verify_accounts_")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    folder = ROOT / "output/accounts" / stamp; folder.mkdir(parents=True, exist_ok=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
    env = {**os.environ, "POSTGRES_DB": name, "SIGNUP_ENABLED": "true"}
    log = (folder / "http-server.log").open("a", encoding="utf-8")
    def start():
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        with httpx.Client(trust_env=False, timeout=2) as probe:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    if probe.get(f"http://127.0.0.1:{port}/health/ready").status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                if process.poll() is not None:
                    raise RuntimeError("temporary_api_exited")
                time.sleep(0.2)
        process.terminate(); process.wait(timeout=10)
        raise RuntimeError("temporary_api_not_ready")
    process = start()
    checks = {}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False) as client:
            password = secrets.token_urlsafe(24)
            body = {"phone": "19900000001", "password": password}
            created = client.post("/api/v1/auth/register", json=body)
            checks["real_registration"] = created.status_code == 201
            first = created.json()
            checks["no_phone_verification_claim"] = first["phone_verified"] is False
            checks["auth_response_not_cached"] = created.headers.get("cache-control") == "no-store"
            checks["duplicate_registration_rejected"] = client.post("/api/v1/auth/register", json=body).status_code == 409
            login = client.post("/api/v1/auth/login", json=body)
            checks["password_login"] = login.status_code == 200 and login.json()["owner_id"] == first["owner_id"]
            auth = {"Authorization": "Bearer " + login.json()["access_token"]}
            checks["authenticated_identity"] = client.get("/api/v1/auth/me", headers=auth).json()["authenticated"] is True
            checks["normal_account_has_no_admin_access"] = client.get("/api/v1/ops/enrichment", headers=auth).status_code == 401
            bad = client.post("/api/v1/auth/login", json={**body, "password": "wrong fictional password!"})
            unknown = client.post("/api/v1/auth/login", json={**body, "phone": "19900000009", "password": "wrong fictional password!"})
            checks["generic_bad_credentials"] = bad.status_code == unknown.status_code == 401 and bad.json()["error"] == unknown.json()["error"]
            other = client.post("/api/v1/auth/register", json={**body, "phone": "19900000002"}).json()
            with get_session_factory()() as db, db.begin():
                account = db.scalar(select(UserAccount).where(UserAccount.phone == "+8619900000001"))
                checks["password_not_stored_plaintext"] = account.password_hash.startswith("scrypt$131072$8$1$") and password not in account.password_hash
                token = db.scalar(select(AccessToken).where(AccessToken.token_hash == hashlib.sha256(first["access_token"].encode()).hexdigest()))
                checks["token_stored_only_as_hash"] = token.account_id == account.id and token.token_hash != first["access_token"]
                conversation = Conversation(owner_id=first["owner_id"], provider="external", is_demo=False, turn_count=0)
                db.add(conversation); db.flush(); conversation_id = conversation.id
            endpoint = f"/api/v1/agent/sessions/{conversation_id}"
            checks["conversation_ownership_enforced"] = client.get(endpoint, headers=auth).status_code == 200 and client.get(
                endpoint, headers={"Authorization": "Bearer " + other["access_token"]}).status_code == 403
            checks["logout_revokes_current_token"] = client.post("/api/v1/auth/logout", headers=auth).status_code == 200 and client.get("/api/v1/auth/me", headers=auth).status_code == 401
            process.terminate(); process.wait(timeout=10); process = start()
            checks["account_persists_after_api_restart"] = client.post("/api/v1/auth/login", json=body).status_code == 200
        with httpx.Client(base_url=f"http://127.0.0.1:{original.api_port}", trust_env=False, timeout=10) as live:
            checks["business_api_ready"] = live.get("/health/ready").status_code == 200
            paths = live.get("/openapi.json").json()["paths"]
            checks["live_api_has_registered_routes"] = all("/api/v1/auth/" + path in paths for path in ("register", "login", "me", "logout"))
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=10)
        log.close()
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()), "checks": checks,
        "isolated_database": name, "fictional_accounts": 2, "business_accounts_created": 0, "model_calls": 0,
        "temporary_api_stopped": True, "passwords_tokens_or_full_phones_printed": False}
    (folder / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(folder / "result.json"), **report}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        raise SystemExit("Account HTTP verification failed: " + type(error).__name__ + "; no credentials printed") from None
