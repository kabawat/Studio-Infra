#!/usr/bin/env python3
"""Provision the initial Redis secrets. Existing credentials are never overwritten."""

import argparse
import sys
from pathlib import Path

from redis_setup.credentials import generate_credentials

REDIS_DIRECTORY = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", action="store_true", help="Generate independent production credentials")
    parser.add_argument("--host", default="redis", help="Redis hostname written to service credential files")
    args = parser.parse_args()
    if not args.host or any(character.isspace() for character in args.host) or "://" in args.host:
        parser.error("--host must be a hostname or IP address, not a URL")
    destination = REDIS_DIRECTORY / "secrets"
    try:
        generate_credentials(destination, host=args.host)
    except FileExistsError:
        print(
            f"Secrets already exist at {destination}; no files overwritten.",
            file=sys.stderr,
        )
        return 1
    except OSError as error:
        print(f"Could not create Redis secrets: {error}", file=sys.stderr)
        return 1
    print(f"Created credentials in {destination}. Keep this directory private.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
