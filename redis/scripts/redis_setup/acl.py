"""Render Redis ACL rules without storing plaintext passwords in the ACL file."""

from collections.abc import Mapping
from hashlib import sha256

SERVICES = ("core", "auth", "utility", "payment", "config")
IDENTITIES = (*SERVICES, "monitor", "operator")

APPLICATION_COMMANDS = (
    "+@read",
    "+@write",
    "+eval",
    "+evalsha",
    "+script|load",
    "+script|exists",
    "+@transaction",
    "+@pubsub",
    "-@dangerous",
    "-keys",
    "-scan",
    "-randomkey",
    "-sort",
    "-sort_ro",
    "-migrate",
    "+ping",
    "+info",
    "+client|setname",
    "+client|setinfo",
    "+client|id",
    "+quit",
)

SHARED_ACCESS = {}


def password_hash(password: str) -> str:
    return sha256(password.encode("utf-8")).hexdigest()


def service_rule(service: str, password: str) -> str:
    """Each service owns its namespace; shared access is added explicitly."""
    channels = f"&{service}:*"
    rules = [
        f"user {service}_user on #{password_hash(password)}",
        f"resetkeys ~{service}:* resetchannels {channels} -@all",
        " ".join(APPLICATION_COMMANDS),
        *SHARED_ACCESS.get(service, ()),
    ]
    return " ".join(rules)


def acl_lines(passwords: Mapping[str, str]) -> list[str]:
    lines = ["user default off resetpass resetkeys resetchannels -@all"]
    lines.extend(service_rule(service, passwords[service]) for service in SERVICES)
    lines.append(
        f"user monitor_user on #{password_hash(passwords['monitor'])} "
        "resetkeys resetchannels -@all +ping +info"
    )
    lines.append(
        f"user operator_user on #{password_hash(passwords['operator'])} "
        "~* &* -@all +@all"
    )
    return lines
