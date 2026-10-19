"""只创建本项目的 .env；保留已有凭据，检查端口，不结束其他进程。"""
from pathlib import Path
import secrets
import socket

ROOT = Path(__file__).resolve().parents[1]


def available_port(preferred):
    for port in range(preferred, preferred + 100):
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free local port near {preferred}")


def main():
    destination = ROOT / ".env"
    if destination.exists():
        print("Existing .env preserved.")
        return
    api, postgres, redis = [available_port(port) for port in (8010, 5440, 6380)]
    values = {
        "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": postgres, "POSTGRES_USER": "backend_dev",
        "POSTGRES_PASSWORD": secrets.token_hex(24), "POSTGRES_DB": "backend_development",
        "REDIS_HOST": "127.0.0.1", "REDIS_PORT": redis, "REDIS_PASSWORD": secrets.token_hex(24),
        "ADMIN_API_KEY": secrets.token_hex(32), "API_PORT": api, "DEMO_HEARTBEAT_SECONDS": 30,
    }
    with destination.open("x", encoding="utf-8") as stream:
        stream.write("# Local random development credentials. Do not commit.\n")
        stream.writelines(f"{key}={value}\n" for key, value in values.items())
    print(f"Local configuration created: API={api}, PostgreSQL={postgres}, Redis={redis}")


if __name__ == "__main__":
    main()
