"""Generate a new credential directory; never rotate existing secrets implicitly."""

import os
import secrets
from pathlib import Path

from .acl import IDENTITIES, SERVICES, acl_lines

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
CONTAINER_SECRET_MODE = 0o444


def write_secret(path: Path, content: str, *, container_readable: bool = False) -> None:
    # Exclusive creation prevents accidental overwrites, including partial setups.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
    if container_readable:
        # Host access is still restricted by the parent directory's 0700 mode.
        path.chmod(CONTAINER_SECRET_MODE)


def service_environment(
    service: str, password: str, *, host: str = "redis"
) -> str:
    settings = {
        "REDIS_HOST": host,
        "REDIS_PORT": "6379",
        "REDIS_USERNAME": f"{service}_user",
        "REDIS_PASSWORD": password,
        "REDIS_DB": "0",
        "REDIS_CONNECT_TIMEOUT_MS": "5000",
        "REDIS_COMMAND_TIMEOUT_MS": "3000",
    }
    return "".join(f"{name}={value}\n" for name, value in settings.items())


def generate_credentials(destination: Path, *, host: str = "redis") -> None:
    destination.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=False)
    passwords = {identity: secrets.token_hex(32) for identity in IDENTITIES}
    write_secret(
        destination / "users.acl",
        "\n".join(acl_lines(passwords)) + "\n",
        container_readable=True,
    )
    write_secret(
        destination / "monitor_password", passwords["monitor"], container_readable=True
    )
    for service in SERVICES:
        write_secret(
            destination / f"{service}.env",
            service_environment(service, passwords[service], host=host),
        )
    write_secret(
        destination / "operator.env", f"REDISCLI_AUTH={passwords['operator']}\n"
    )
