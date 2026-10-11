"""Start/stop the real local frontend, PostgreSQL backend, Redis, and RAG Agent.

Run with the Agent venv Python. All credentials/data/logs are project-local and
ignored by Git. No production data or existing system services are changed.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "reports/local/fullstack"
PRIVATE = ROOT / ".secrets/fullstack.env"
STATE = LOCAL / "processes.json"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def write_private(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as target:
        target.write(content)
    path.chmod(0o600)


def read_env(path: Path):
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def configuration():
    if not PRIVATE.exists():
        values = {"POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "5440",
                  "POSTGRES_USER": "ict_demo", "POSTGRES_DB": "ict_fullstack",
                  "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6380", "API_PORT": "8010",
                  "AGENT_SERVICE_URL": "http://127.0.0.1:9001", "INDEX_PROVIDER": "external",
                  "DEMO_HEARTBEAT_SECONDS": "30"}
        for key in ["POSTGRES_PASSWORD", "REDIS_PASSWORD", "ADMIN_API_KEY", "AGENT_SERVICE_TOKEN",
                    "AGENT_TOOL_TOKEN", "AGENT_WRITE_TOKEN"]:
            values[key] = secrets.token_hex(32)
        write_private(PRIVATE, "# Local integration credentials; never commit.\n" +
                      "".join(f"{key}={value}\n" for key, value in values.items()))
    env = os.environ.copy()
    env.update(read_env(PRIVATE))
    env.update({"PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    return env


def project(module: str, ref: str):
    explicit = os.environ.get(f"ICT_{module.upper()}_DIR")
    candidates = [Path(explicit)] if explicit else [ROOT / module, ROOT / "reports/local/integration" / module]
    for path in candidates:
        if (path / ("package.json" if module == "frontend" else "backend/main.py")).exists():
            return path.resolve()
    destination = candidates[-1]
    # Only create absent runtime copies. Preserve teammate edits on every rerun.
    if destination.exists():
        raise RuntimeError(f"Incomplete {module} directory: {destination}")
    raw = subprocess.check_output(["git", "archive", ref, module], cwd=ROOT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            target = (destination.parent / member.name).resolve()
            if not target.is_relative_to(destination.parent.resolve()) or member.issym() or member.islnk():
                raise RuntimeError("Unsafe project archive member")
        archive.extractall(destination.parent)
    return destination


def paths():
    backend = project("backend", "origin/feature/backend-integration")
    frontend = project("frontend", "origin/codex/frontend-demo")
    pg = Path(os.environ.get("ICT_PG_BIN", ROOT / ".cache/fullstack/postgresql-16.15-install/bin"))
    redis = Path(os.environ.get("ICT_REDIS_BIN", ROOT / ".cache/fullstack/redis-7.4.10-install/bin"))
    node = shutil.which("node") or str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node")
    return backend, frontend, pg, redis, node


def request(path: str, env, *, method="GET", body=None, user=None, admin=False):
    headers = {"Content-Type": "application/json"}
    if admin:
        headers["X-API-Key"] = env["ADMIN_API_KEY"]
    if user:
        headers["Authorization"] = "Bearer " + user
    url = "http://127.0.0.1:8010" + path
    payload = json.dumps(body).encode() if body is not None else None
    try:
        with OPENER.open(urllib.request.Request(url, data=payload, headers=headers, method=method), timeout=40) as response:
            raw = response.read(2 * 1024 * 1024)
            return json.loads(raw) if raw else {}, dict(response.headers)
    except urllib.error.HTTPError as error:
        # Do not emit arbitrary provider/secret-bearing error bodies.
        raise RuntimeError(f"Local backend {path.split('?')[0]} returned HTTP {error.code}") from None


def token(env):
    data, _ = request("/api/v1/auth/tokens", env, method="POST", admin=True,
                      body={"owner_id": "local-demo", "expires_in_hours": 24})
    write_private(LOCAL / "session.json", json.dumps({"access_token": data["access_token"],
                  "expires_at": data["expires_at"], "is_demo": False}, indent=2) + "\n")
    print("Local browser session issued (24h); credentials saved privately.", flush=True)


def frontend_environment(env, node):
    # The frontend's development health bridge reads only this existing service
    # credential on the server. Database/admin/model keys stay out of the file.
    agent_status_file = PRIVATE.with_name("fullstack-agent-status.env")
    write_private(agent_status_file, "RAG_SERVICE_TOKEN=" + env["AGENT_SERVICE_TOKEN"] + "\n")
    return {**env, "ICT_LOCAL_SESSION_FILE": str(LOCAL / "session.json"),
            "ICT_BACKEND_ORIGIN": "http://127.0.0.1:8010",
            "ICT_LOCAL_AGENT_ORIGIN": "http://127.0.0.1:9001",
            "ICT_LOCAL_AGENT_ENV_FILE": str(agent_status_file),
            "PATH": str(Path(node).parent) + os.pathsep + env.get("PATH", "")}


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"processes": []}


def save_state(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(STATE)


def owns_process(row):
    try:
        actual = subprocess.check_output(["ps", "-p", str(row["pid"]), "-o", "args="], text=True).strip()
        return (row["signature"] in actual and str(ROOT) in actual
                and os.getpgid(row["pid"]) == row["pid"])
    except (subprocess.CalledProcessError, ProcessLookupError):
        return False


def owns_agent(row):
    if (row.get("name") != "agent" or row.get("signature") != str(ROOT / ".venv/bin/python")
            or not owns_process(row)):
        return False
    try:
        actual = subprocess.check_output(["ps", "-p", str(row["pid"]), "-o", "args="], text=True).strip()
        args = shlex.split(actual)
        return ("uvicorn" in args and "examples.backend_api:app" in args
                and "--app-dir" in args and args[args.index("--app-dir") + 1] == str(ROOT)
                and "--port" in args and args[args.index("--port") + 1] == "9001")
    except (subprocess.CalledProcessError, ValueError, IndexError):
        return False


def stop_rows(rows):
    for row in reversed(rows):
        if owns_process(row):
            try:
                os.killpg(row["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any(owns_process(row) for row in rows):
        time.sleep(0.2)
    for row in reversed(rows):
        if owns_process(row):
            # PostgreSQL SIGINT performs a fast, durable shutdown; never SIGKILL.
            try:
                os.killpg(row["pid"], signal.SIGINT)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any(owns_process(row) for row in rows):
        time.sleep(0.2)
    return [row for row in rows if owns_process(row)]


def stop(state=None):
    state = load_state() if state is None else state
    survivors = stop_rows(state["processes"])
    if survivors:
        state["processes"] = survivors
        save_state(state)
        raise RuntimeError("Services still stopping; state retained: " + ", ".join(row["name"] for row in survivors))
    if STATE.exists():
        STATE.unlink()
    print("Project services stopped; database and index retained.", flush=True)


def check_port(port):
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port {port} already in use; existing process preserved")


def wait_http(url, headers=None, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with OPENER.open(urllib.request.Request(url, headers=headers or {}), timeout=3) as response:
                if response.status == 200:
                    return json.loads(response.read(16000))
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Service not ready: {url}; see {LOCAL / 'logs'}")


def agent_environment(env, backend, *, live):
    """Build the same Agent configuration for full startup and replacement."""
    rag_env = {**env, "PYTHONPATH": str(ROOT / "packages/rag_agent/src"),
               "HF_HOME": str(ROOT / ".cache/huggingface"), "HF_HUB_OFFLINE": "1",
               "RAG_BACKEND_DB_PATH": str(ROOT / "data/backend-rag.db"),
               "RAG_BACKEND_URL": "http://127.0.0.1:8010",
               "RAG_SERVICE_TOKEN": env["AGENT_SERVICE_TOKEN"],
               "RAG_BACKEND_TOOL_TOKEN": env["AGENT_TOOL_TOKEN"],
               "RAG_BACKEND_WRITE_TOKEN": env["AGENT_WRITE_TOKEN"],
               "RAG_LLM_MODE": "live" if live else "extractive",
               "RAG_LLM_BASE_URL": env.get("RAG_LLM_BASE_URL", "https://api.deepseek.com"),
               "RAG_LLM_MODEL": env.get("RAG_LLM_MODEL", "deepseek-flash")}
    key = env.get("RAG_LLM_KEY")
    key_file = env.get("RAG_LLM_KEY_FILE")
    if key and key_file:
        raise RuntimeError("Configure RAG_LLM_KEY or RAG_LLM_KEY_FILE, not both; services preserved")
    if not live:
        # Extractive startup and rollback require no provider credentials.
        rag_env.pop("RAG_LLM_KEY", None)
        rag_env.pop("RAG_LLM_KEY_FILE", None)
        return rag_env
    default_key_file = ROOT / ".secrets/llm-api-key"
    if not key and not key_file and default_key_file.exists():
        key_file = str(default_key_file)
        rag_env["RAG_LLM_KEY_FILE"] = key_file
    if not key and not key_file:
        # Existing teammate configuration is the last fallback, used only in
        # process memory. An explicit file never competes with this fallback.
        key = read_env(backend / ".env").get("DEEPSEEK_API_KEY")
        if key:
            rag_env["RAG_LLM_KEY"] = key
        else:
            raise RuntimeError("Live mode needs RAG_LLM_KEY, RAG_LLM_KEY_FILE, "
                               ".secrets/llm-api-key, or the backend DeepSeek key; services preserved")
    return rag_env


def preflight_agent(rag_env):
    python_agent = ROOT / ".venv/bin/python"
    if not python_agent.exists():
        raise RuntimeError("Missing Agent runtime; services preserved; see docs/fullstack_local.md")
    # Settings validation also checks key-file readability/format. It imports
    # no provider and makes no model request. Suppress all child output so even
    # validation exceptions cannot disclose credentials.
    code = ("from rag_agent.config import Settings\n"
            "try:\n    Settings.from_env()\n"
            "except Exception:\n    raise SystemExit(1)\n")
    try:
        result = subprocess.run([str(python_agent), "-c", code], cwd=ROOT, env=rag_env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Agent configuration preflight timed out; services preserved") from None
    if result.returncode:
        raise RuntimeError("Agent configuration preflight failed; check model URL, mode, "
                           "and credential configuration; services preserved")


def spawn_process(state, name, command, cwd, child_env):
    with (LOCAL / "logs" / f"{name}.log").open("a") as output:
        child = subprocess.Popen(command, cwd=cwd, env=child_env, stdout=output,
                                 stderr=subprocess.STDOUT, start_new_session=True)
    signature = "ICT-RAG" if name == "redis" else str(command[0]) if name == "postgres" else next(
        (str(value) for value in command if str(ROOT) in str(value)), str(command[0]))
    row = {"name": name, "pid": child.pid, "signature": signature}
    state["processes"].append(row)
    save_state(state)
    return row


def spawn_agent(state, rag_env):
    python_agent = str(ROOT / ".venv/bin/python")
    row = spawn_process(state, "agent", [python_agent, "-m", "uvicorn", "examples.backend_api:app",
                        "--app-dir", str(ROOT), "--host", "127.0.0.1", "--port", "9001"], ROOT, rag_env)
    health = wait_http("http://127.0.0.1:9001/v1/health",
                       {"Authorization": "Bearer " + rag_env["RAG_SERVICE_TOKEN"]})
    if not owns_agent(row) or health.get("llm_mode") != rag_env["RAG_LLM_MODE"]:
        raise RuntimeError("Agent health did not confirm the owned process and requested generation mode")
    return row


def restart_agent(args):
    state = load_state()
    agent_rows = [row for row in state["processes"] if row["name"] == "agent"]
    if len(agent_rows) != 1:
        raise RuntimeError("Need exactly one recorded Agent; services preserved; use status")
    old_row = agent_rows[0]
    if not owns_agent(old_row):
        raise RuntimeError("Recorded Agent ownership could not be confirmed; services preserved")
    if not PRIVATE.exists():
        raise RuntimeError("Existing local integration credentials are missing; services preserved")
    backend = Path(state.get("backend_dir", ""))
    if not (backend / "backend/main.py").exists():
        raise RuntimeError("Recorded backend runtime directory is unavailable; services preserved")
    env = configuration()
    rag_env = agent_environment(env, backend, live=args.live)
    preflight_agent(rag_env)
    old_mode = state.get("llm_mode")
    old_env = None
    if old_mode in {"live", "extractive"}:
        try:
            old_env = agent_environment(env, backend, live=old_mode == "live")
            preflight_agent(old_env)
        except RuntimeError:
            # Replacement may still succeed (for example live -> extractive
            # after removing an invalid key), but rollback is then unavailable.
            old_env = None
    (LOCAL / "logs").mkdir(parents=True, exist_ok=True)
    state["agent_status"] = "restarting"
    state["agent_requested_mode"] = rag_env["RAG_LLM_MODE"]
    save_state(state)
    if stop_rows([old_row]):
        state["agent_status"] = "stop_failed"
        state.pop("agent_requested_mode", None)
        save_state(state)
        raise RuntimeError("Old Agent still running; other services preserved; replacement was not started")
    state["processes"].remove(old_row)
    state["llm_mode"] = None
    state["agent_status"] = "starting"
    save_state(state)
    try:
        check_port(9001)
        spawn_agent(state, rag_env)
    except BaseException:
        failed_rows = [row for row in state["processes"] if row["name"] == "agent"]
        survivors = stop_rows(failed_rows)
        state["processes"] = [row for row in state["processes"] if row["name"] != "agent"] + survivors
        state["llm_mode"] = None
        state["agent_status"] = "stopping_failed" if survivors else "unavailable"
        state.pop("agent_requested_mode", None)
        save_state(state)
        if old_env is not None and not survivors:
            try:
                check_port(9001)
                state["agent_status"] = "restoring"
                save_state(state)
                spawn_agent(state, old_env)
                state.update(llm_mode=old_mode, agent_status="ready")
                save_state(state)
            except BaseException:
                restore_rows = [row for row in state["processes"] if row["name"] == "agent"]
                restore_survivors = stop_rows(restore_rows)
                state["processes"] = [row for row in state["processes"] if row["name"] != "agent"] + restore_survivors
                state.update(llm_mode=None, agent_status="stopping_failed" if restore_survivors else "unavailable")
                save_state(state)
            else:
                raise RuntimeError("Requested Agent restart failed; previous generation mode restored "
                                   "with current configuration; other services preserved; see logs/agent.log") from None
        raise RuntimeError("Agent restart failed and Agent is not ready; other services preserved; "
                           "state retained; see logs/agent.log") from None
    state.update(llm_mode=rag_env["RAG_LLM_MODE"], agent_status="ready")
    state.pop("agent_requested_mode", None)
    save_state(state)
    print(f"AGENT READY http://127.0.0.1:9001 | generation={state['llm_mode']} | "
          "other project services retained; provider request not yet verified", flush=True)


def start(args):
    owned_state = {"processes": []}
    try:
        _start(args, owned_state)
    except BaseException:
        if owned_state["processes"]:
            managed = load_state()
            stop(managed if managed.get("supervisor_pid") == os.getpid() else owned_state)
        raise


def _start(args, state):
    LOCAL.mkdir(parents=True, exist_ok=True)
    (LOCAL / "logs").mkdir(exist_ok=True)
    env = configuration()
    backend, frontend, pg, redis, node = paths()
    for required in [pg / "postgres", redis / "redis-server", backend / ".venv/bin/python", ROOT / ".venv/bin/python", Path(node)]:
        if not required.exists():
            raise RuntimeError(f"Missing runtime: {required}; see docs/fullstack_local.md")
    if any(owns_process(row) for row in load_state()["processes"]):
        raise RuntimeError("Project processes already running; use status or stop first")
    for port in [5440, 6380, 8010, 9001, 5173]:
        check_port(port)
    rag_env = agent_environment(env, backend, live=args.live)
    preflight_agent(rag_env)
    state.update(llm_mode=None, agent_status="starting", backend_dir=str(backend),
                 frontend_dir=str(frontend), supervisor_pid=None if args.detach else os.getpid())

    def spawn(name, command, cwd, child_env=env):
        return spawn_process(state, name, command, cwd, child_env)

    pgdata = ROOT / ".cache/fullstack/postgres-data"
    redisdata = ROOT / ".cache/fullstack/redis-data"
    redisdata.mkdir(parents=True, exist_ok=True)
    if not (pgdata / "PG_VERSION").exists():
        password_file = LOCAL / "initdb.password"
        write_private(password_file, env["POSTGRES_PASSWORD"] + "\n")
        try:
            with (LOCAL / "logs/initdb.log").open("w") as log:
                subprocess.run([str(pg / "initdb"), "-D", str(pgdata), "-U", env["POSTGRES_USER"],
                                "--pwfile", str(password_file), "--auth-local=scram-sha-256",
                                "--auth-host=scram-sha-256", "--encoding=UTF8", "--locale=C"],
                               stdout=log, stderr=subprocess.STDOUT, check=True)
        finally:
            password_file.unlink(missing_ok=True)
    spawn("postgres", [str(pg / "postgres"), "-D", str(pgdata), "-h", "127.0.0.1", "-p", "5440",
                       "-k", str(LOCAL), "-c", "max_connections=40"], ROOT)
    pg_env = {**env, "PGHOST": "127.0.0.1", "PGPORT": "5440", "PGUSER": env["POSTGRES_USER"],
              "PGPASSWORD": env["POSTGRES_PASSWORD"]}
    for _ in range(40):
        ready = subprocess.run([str(pg / "pg_isready"), "-h", "127.0.0.1", "-p", "5440"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ready.returncode == 0:
            break
        time.sleep(0.25)
    existing = subprocess.check_output([str(pg / "psql"), "-d", "postgres", "-tAc",
        "SELECT 1 FROM pg_database WHERE datname='ict_fullstack'"], env=pg_env, text=True).strip()
    if existing != "1":
        subprocess.run([str(pg / "createdb"), "ict_fullstack"], env=pg_env, check=True)
    redis_config = LOCAL / "redis.conf"
    write_private(redis_config, f"bind 127.0.0.1\nport 6380\nprotected-mode yes\nrequirepass {env['REDIS_PASSWORD']}\n"
                  f'dir {redisdata}\nappendonly yes\nappendfsync everysec\nproc-title-template "ICT-RAG {ROOT} {{listen-addr}}"\n')
    spawn("redis", [str(redis / "redis-server"), str(redis_config)], ROOT)
    time.sleep(0.5)
    python_backend = str(backend / ".venv/bin/python")
    with (LOCAL / "logs/migrate.log").open("a") as log:
        subprocess.run([python_backend, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run([python_backend, "-m", "backend.bootstrap"], cwd=backend, env=env,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    spawn("backend", [python_backend, "-m", "uvicorn", "backend.main:app", "--app-dir", str(backend),
                      "--host", "127.0.0.1", "--port", "8010"], backend)
    wait_http("http://127.0.0.1:8010/health/ready")
    spawn("worker", [python_backend, "-m", "celery", "-A", "backend.tasks.celery_app:celery_app",
                     "worker", "--loglevel=WARNING", "--pool=solo", "--hostname=ict-local@localhost"], backend)
    spawn("beat", [python_backend, "-m", "celery", "-A", "backend.tasks.celery_app:celery_app",
                   "beat", "--loglevel=WARNING", "--schedule", str(LOCAL / "celerybeat")], backend)
    python_agent = str(ROOT / ".venv/bin/python")
    spawn_agent(state, rag_env)
    state.update(llm_mode=rag_env["RAG_LLM_MODE"], agent_status="ready")
    save_state(state)
    sync_env = {**rag_env, "RAG_LLM_MODE": "extractive"}
    sync_env.pop("RAG_LLM_KEY", None)
    sync_env.pop("RAG_LLM_KEY_FILE", None)
    spawn("sync", [python_agent, str(ROOT / "scripts/sync_backend.py"), "--all-scopes", "--poll-interval", "5"], ROOT,
          sync_env)
    token(env)
    frontend_env = frontend_environment(env, node)
    spawn("frontend", [node, str(frontend / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1",
                       "--port", "5173", "--strictPort"], frontend, frontend_env)
    # Frontend responds with HTML; use a separate readiness check.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with OPENER.open("http://127.0.0.1:5173", timeout=2) as response:
                if response.status == 200:
                    break
        except urllib.error.URLError:
            time.sleep(0.5)
    else:
        raise RuntimeError("Frontend not ready; see logs/frontend.log")
    save_state(state)
    print(f"READY http://127.0.0.1:5173 | real PostgreSQL/Redis/E5 | generation={state['llm_mode']}", flush=True)
    if not args.detach:
        try:
            while True:
                time.sleep(2)
                # Agent-only replacement updates the shared registry. Monitor
                # that registry rather than the old Agent PID held in memory.
                managed = load_state()
                replacing = managed.get("agent_status") in {
                    "restarting", "starting", "restoring", "unavailable", "stopping_failed", "stop_failed"}
                failed = [row["name"] for row in managed["processes"]
                          if not (row["name"] == "agent" and replacing) and not owns_process(row)]
                if not replacing and not any(row["name"] == "agent" for row in managed["processes"]):
                    failed.append("agent")
                if failed:
                    raise RuntimeError("Project processes stopped: " + ", ".join(failed))
        except KeyboardInterrupt:
            stop()


def status():
    state = load_state()
    print(json.dumps({"generation": state.get("llm_mode"), "agent_status": state.get("agent_status"),
        "requested_generation": state.get("agent_requested_mode"), "processes": [
        {"name": row["name"], "running": owns_process(row)} for row in state["processes"]]}, indent=2))


def seed():
    env = configuration()
    sources, _ = request("/api/v1/sources", env)
    # Bounded public source sampling. Scheduling remains disabled unless enabled
    # by an administrator; each command creates one source collection task.
    choices = {"osv": {"packages": ["langchain"], "max_pages": 1},
               "nist": {}, "china_policy": {}}
    for source in sources:
        if source["adapter"] in choices:
            request(f"/api/v1/sources/{source['id']}", env, method="PATCH", admin=True,
                    body={"config": choices[source["adapter"]], "enabled": False})
            task, _ = request(f"/api/v1/sources/{source['id']}/collect", env, method="POST", admin=True)
            print(json.dumps({"source": source["adapter"], "task_id": task["id"], "status": task["status"]}), flush=True)
    task, _ = request("/api/v1/demo/import", env, method="POST", admin=True)
    print(json.dumps({"source": "explicit_synthetic_demo", "task_id": task["id"], "status": task["status"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "restart-agent", "stop", "status", "token", "seed"])
    parser.add_argument("--live", action="store_true", help="Enable configured provider (default Flash); queries can incur provider charges")
    parser.add_argument("--detach", action="store_true", help="Leave only this project's child services running")
    args = parser.parse_args()
    try:
        if args.command == "start":
            start(args)
        elif args.command == "restart-agent":
            restart_agent(args)
        elif args.command == "stop":
            stop()
        elif args.command == "status":
            status()
        elif args.command == "seed":
            seed()
        else:
            token(configuration())
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(str(error) if isinstance(error, RuntimeError) else "Runtime command failed; inspect project logs.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
