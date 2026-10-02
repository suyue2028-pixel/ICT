"""Add independent internal read/write tokens; preserve every existing .env value."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
content = path.read_text(encoding="utf-8")
existing = {line.split("=", 1)[0].strip() for line in content.splitlines() if "=" in line and not line.lstrip().startswith("#")}
additions = []
for name in ("AGENT_TOOL_TOKEN", "AGENT_WRITE_TOKEN"):
    if name not in existing:
        additions.append(name + "=" + secrets.token_urlsafe(48))
if additions:
    with path.open("a", encoding="utf-8") as target:
        target.write("\n" + "\n".join(additions) + "\n")
print("Internal credentials configured; existing settings preserved; no credentials displayed")
