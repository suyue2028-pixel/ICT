"""Backup and deploy this source using existing Compose volumes and Agent overlay."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DOCKER = Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
OUTPUT = ROOT / "output"
OVERLAY = Path(r"D:\cit\ICT-team\reports\local\windows-fullstack\compose.windows.json")
PRIVATE = Path(r"D:\cit\ICT-team\.secrets\windows-integration.env")


def read_env(path):
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def main():
    OUTPUT.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    env = {**os.environ, **read_env(ROOT / ".env")}
    if PRIVATE.exists():
        env.update(read_env(PRIVATE))
    command = [str(DOCKER), "compose", "--project-directory", str(ROOT),
               "--env-file", str(ROOT / ".env"), "-f", str(ROOT / "compose.yaml")]
    if OVERLAY.exists():
        shutil.copy2(OVERLAY, OUTPUT / ("compose-before-timeliness-" + stamp + ".json"))
        data = json.loads(OVERLAY.read_text(encoding="utf-8"))
        data["services"]["migrate"]["build"]["context"] = str(ROOT)
        OVERLAY.write_text(json.dumps(data, indent=2), encoding="utf-8")
        command += ["-f", str(OVERLAY)]
    log_path = OUTPUT / ("timeliness-deploy-" + stamp + ".log")
    with log_path.open("w", encoding="utf-8") as log:
        def compose(*args):
            subprocess.run([*command, *args], cwd=ROOT, env=env, stdout=log, stderr=log, check=True)
        compose("config", "--quiet")
        dump = OUTPUT / ("pre-timeliness-" + stamp + ".dump")
        with dump.open("xb") as handle:
            subprocess.run([str(DOCKER), "exec", "cit-backend-development-postgres-1", "pg_dump",
                "-U", env["POSTGRES_USER"], "-d", env["POSTGRES_DB"], "-Fc"],
                env=env, stdout=handle, stderr=log, check=True)
        with dump.open("rb") as handle:
            subprocess.run([str(DOCKER), "exec", "-i", "cit-backend-development-postgres-1",
                "pg_restore", "--list"], env=env, stdin=handle, stdout=log, stderr=log, check=True)
        subprocess.run([str(DOCKER), "tag", "cit-backend-development:local",
            "cit-backend-development:pre-timeliness-" + stamp.lower()], stdout=log, stderr=log, check=True)
        compose("build", "migrate")
        compose("stop", "--timeout", "60", "api", "worker", "beat")
        compose("run", "--rm", "migrate")
        compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "api", "worker", "beat")
    print(json.dumps({"deployed_source": str(ROOT), "backup": str(dump), "log": str(log_path),
        "volumes_retained": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
