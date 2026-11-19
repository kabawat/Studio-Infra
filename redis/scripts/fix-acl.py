#!/usr/bin/env python3
"""Re-render users.acl from existing service secret files and reload Redis."""

import subprocess
import sys
from pathlib import Path

REDIS_DIR = Path(__file__).resolve().parents[1]
SECRETS_DIR = REDIS_DIR / "secrets"
ACL_FILE = SECRETS_DIR / "users.acl"

sys.path.insert(0, str(REDIS_DIR / "scripts"))
from redis_setup.acl import IDENTITIES, SERVICES, acl_lines


def read_password(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("REDIS_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise ValueError(f"Missing REDIS_PASSWORD in {path}")


def main():
    if not SECRETS_DIR.is_dir():
        print(f"Error: {SECRETS_DIR} not found.", file=sys.stderr)
        return 1

    passwords = {}
    for service in SERVICES:
        passwords[service] = read_password(SECRETS_DIR / f"{service}.env")
    passwords["monitor"] = (SECRETS_DIR / "monitor_password").read_text().strip()
    for line in (SECRETS_DIR / "operator.env").read_text().splitlines():
        if line.startswith("REDISCLI_AUTH="):
            passwords["operator"] = line.split("=", 1)[1].strip()
            break

    missing = [identity for identity in IDENTITIES if not passwords.get(identity)]
    if missing:
        print(f"Error: missing passwords for {missing}", file=sys.stderr)
        return 1

    if ACL_FILE.exists():
        ACL_FILE.chmod(0o644)
    ACL_FILE.write_text("\n".join(acl_lines(passwords)) + "\n")
    ACL_FILE.chmod(0o444)
    print(f"Successfully updated {ACL_FILE}")

    for container in ["redis", "redis-prod"]:
        check = subprocess.run(
            ["docker", "container", "inspect", "-f", "{{.State.Running}}", container],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if check.returncode == 0 and "true" in check.stdout.strip().lower():
            print(f"Restarting container '{container}' to load updated ACL...")
            restart = subprocess.run(["docker", "restart", container], capture_output=True, text=True)
            if restart.returncode == 0:
                print(f"Container '{container}' restarted successfully!")
            else:
                print(f"Warning: Failed to restart {container}: {restart.stderr}", file=sys.stderr)
            break
    else:
        print("Note: Docker container restart skipped (container not running or docker not reachable here).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

