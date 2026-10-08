"""Export fixed team branch modules into ignored, absent-only runtime copies."""
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "reports/local/integration"


def main():
    versions = {}
    for module, branch in [("frontend", "codex/frontend-demo"), ("backend", "feature/backend-integration")]:
        ref = "origin/" + branch
        command = ["git", "-c", "safe.directory=" + ROOT.as_posix()]
        commit = subprocess.check_output(command + ["rev-parse", ref], cwd=ROOT, text=True).strip()
        destination = LOCAL / module
        if destination.exists():
            raise RuntimeError("Existing runtime module retained: " + str(destination))
        raw = subprocess.check_output(command + ["archive", ref, module], cwd=ROOT)
        LOCAL.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                target = (LOCAL / member.name).resolve()
                if not target.is_relative_to(LOCAL.resolve()) or member.issym() or member.islnk():
                    raise RuntimeError("Unsafe archive member")
            archive.extractall(LOCAL, filter="data")
        versions[module] = {"branch": branch, "commit": commit, "directory": str(destination)}
    versions["agent"] = {"branch": "agent-dev", "commit": subprocess.check_output(
        ["git", "-c", "safe.directory=" + ROOT.as_posix(), "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    (LOCAL / "windows-branches.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    print(json.dumps(versions, indent=2))


if __name__ == "__main__":
    main()
