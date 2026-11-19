#!/usr/bin/env python3
"""Sync generated Redis credentials into the backend development env files."""

import argparse
import os
import stat
import sys
from pathlib import Path

SERVICES = ("auth", "core", "payment", "utility", "config")
SERVICE_DIRECTORIES = {
    "auth": "studio-auth",
    "core": "studio-core",
    "payment": "studio-payment",
    "utility": "studio-utility",
    "config": "config",
}
REMOVED_KEYS = {"REDIS_KEY_PREFIX", "REDIS_MAX_RETRIES"}


def read_settings(path: Path, *, allow_duplicates: bool = False) -> dict[str, str]:
    settings = {}
    line_numbers = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid setting in {path}:{number}: missing '=' in line {line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid setting in {path}:{number}: empty key in line {line!r}")
        if key in settings and not allow_duplicates:
            raise ValueError(
                f"Duplicate setting '{key}' in {path}:{number} (previously defined at line {line_numbers[key]})"
            )
        settings[key] = value
        line_numbers[key] = number
    return settings


def write_settings(path: Path, updates: dict[str, str], *, remove_keys: set[str] | None = None) -> None:
    original = path.read_text()
    lines = []
    written = set()
    removed = remove_keys or set()
    for line in original.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in removed:
                continue
            if key in updates:
                line = f"{key}={updates[key]}"
                written.add(key)
        lines.append(line)
    lines.extend(
        f"{key}={value}" for key, value in updates.items() if key not in written
    )
    mode = stat.S_IMODE(path.stat().st_mode)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(mode)


def resolve_service_directory(backend_root: Path, service: str) -> Path:
    env_var_name = f"{service.upper()}_DIR"
    custom_dir = os.environ.get(env_var_name)
    if custom_dir:
        return Path(custom_dir)
    for candidate in (f"studio-{service}", service, SERVICE_DIRECTORIES[service]):
        if (backend_root / candidate).is_dir():
            return backend_root / candidate
    return backend_root / SERVICE_DIRECTORIES[service]


def configure(
    infra_root: Path, backend_root: Path, redis_host: str, *, production: bool = False
) -> None:
    del production  # All environments use the same studio-* / config directory layout.
    secrets_directory = infra_root / "redis" / "secrets"
    configuration = []
    for service in SERVICES:
        secret_file = secrets_directory / f"{service}.env"
        secret_settings = read_settings(secret_file)
        secret_settings["REDIS_HOST"] = redis_host

        service_directory = resolve_service_directory(backend_root, service)
        env_file = service_directory / ".env"
        if not env_file.exists():
            print(f"Notice: {env_file} does not exist; skipping sync for {service}.", file=sys.stderr)
            continue
        read_settings(env_file, allow_duplicates=True)
        updates = {
            key: value
            for key, value in secret_settings.items()
            if key.startswith("REDIS_")
        }
        configuration.append((secret_file, secret_settings, env_file, updates))

    for secret_file, secret_settings, env_file, updates in configuration:
        write_settings(secret_file, secret_settings)
        write_settings(env_file, updates, remove_keys=REMOVED_KEYS)
        env_file.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--infra-root", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument(
        "--production",
        action="store_true",
        help="Use production secrets and unprefixed service directories",
    )
    args = parser.parse_args()
    if not args.host or any(character.isspace() for character in args.host) or "://" in args.host:
        parser.error("--host must be a hostname or IP address, not a URL")
    try:
        configure(
            args.infra_root, args.backend_root, args.host, production=args.production
        )
    except (OSError, ValueError) as error:
        print(f"Could not configure Redis credentials: {error}", file=sys.stderr)
        return 1
    print("Redis credentials configured in service .env files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
