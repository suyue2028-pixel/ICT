"""Build project-local PostgreSQL/Redis from verified official release archives.

No system packages, launch agents, or existing database directories are changed.
Requires a C compiler and make; downloads/build output stay in ignored .cache.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "fullstack"
PG_VERSION = "16.15"
REDIS_VERSION = "7.4.10"
REDIS_SHA256 = "669ab6689b5e7d0c479e8d526ccbadae36b69a11370742ffe23822b9df8d85ba"


def run(command, *, cwd=None, log):
    result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Build command failed ({command[0]}); see {log.name}")


def download(url: str, destination: Path, log):
    temporary = destination.with_suffix(destination.suffix + ".part")
    run(["curl", "--fail", "--location", "--retry", "2", "--max-time", "180",
         "--output", str(temporary), url], log=log)
    temporary.replace(destination)


def extract(archive: Path, destination: Path):
    # Reject links and paths outside the build directory, including traversal.
    with tarfile.open(archive) as source:
        members = source.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or member.issym() or member.islnk():
                raise RuntimeError("Unsafe source archive member")
        source.extractall(destination, members=members)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = str(min(4, os.cpu_count() or 2))
    receipt = {}
    with (CACHE / "build.log").open("a", encoding="utf-8") as log:
        for name, version, expected in [("postgresql", PG_VERSION, None),
                                        ("redis", REDIS_VERSION, REDIS_SHA256)]:
            prefix = CACHE / f"{name}-{version}-install"
            binary = prefix / "bin" / ("postgres" if name == "postgresql" else "redis-server")
            if binary.exists():
                print(f"{name} {version} already installed: {prefix}", flush=True)
                receipt[name] = {"version": version, "prefix": str(prefix)}
                continue
            suffix = ".tar.bz2" if name == "postgresql" else ".tar.gz"
            archive = CACHE / f"{name}-{version}{suffix}"
            url = (f"https://ftp.postgresql.org/pub/source/v{version}/{archive.name}" if name == "postgresql"
                   else f"https://download.redis.io/releases/{archive.name}")
            if expected is None:
                checksum = CACHE / (archive.name + ".sha256")
                download(url + ".sha256", checksum, log)
                match = re.search(r"\b[a-f0-9]{64}\b", checksum.read_text())
                if not match:
                    raise RuntimeError("Official PostgreSQL checksum missing")
                expected = match.group(0)
            if not archive.exists():
                print(f"Downloading official {name} {version}", flush=True)
                download(url, archive, log)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            if digest != expected:
                raise RuntimeError(f"{name} source checksum mismatch")
            source = CACHE / f"{name}-{version}"
            if not source.exists():
                extract(archive, CACHE)
            print(f"Building {name} {version} ({jobs} compiler jobs)", flush=True)
            if name == "postgresql":
                run(["./configure", f"--prefix={prefix}", "--without-icu", "--without-readline",
                     "--without-zlib"], cwd=source, log=log)
                run(["make", "-j", jobs], cwd=source, log=log)
                run(["make", "install"], cwd=source, log=log)
            else:
                run(["make", "-j", jobs, "MALLOC=libc", "BUILD_TLS=no"], cwd=source, log=log)
                run(["make", "install", f"PREFIX={prefix}"], cwd=source, log=log)
            receipt[name] = {"version": version, "prefix": str(prefix), "source_url": url,
                             "sha256": digest}
            print(f"Installed {name} {version}", flush=True)
    (CACHE / "services.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("Local service binaries ready; start with scripts/run_fullstack.py", flush=True)


if __name__ == "__main__":
    main()
