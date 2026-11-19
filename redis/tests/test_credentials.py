"""Credential provisioning contracts; no Docker or external services required."""

import stat
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIRECTORY = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))
from redis_setup.acl import IDENTITIES, SERVICES, acl_lines
from redis_setup.credentials import generate_credentials


class CredentialTests(unittest.TestCase):
    def test_acl_preserves_service_boundaries(self):
        passwords = {name: f"test-{name}" for name in IDENTITIES}
        rules = acl_lines(passwords)
        self.assertEqual(len(rules), 8)
        self.assertEqual(
            rules[0], "user default off resetpass resetkeys resetchannels -@all"
        )
        for service, rule in zip(SERVICES, rules[1:6], strict=True):
            self.assertIn(f"user {service}_user on #", rule)
            self.assertIn(f"~{service}:*", rule)
            self.assertIn(f"&{service}:*", rule)
            self.assertIn("-@dangerous -keys -scan -randomkey", rule)
            self.assertNotIn(passwords[service], rule)
        self.assertIn("resetkeys resetchannels -@all +ping +info", rules[6])
        self.assertIn("~* &* -@all +@all", rules[7])

    def test_generated_files_have_private_permissions_and_unique_passwords(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "secrets"
            generate_credentials(destination)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            passwords = set()
            for service in SERVICES:
                path = destination / f"{service}.env"
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                values = dict(
                    line.split("=", 1) for line in path.read_text().splitlines()
                )
                self.assertEqual(values["REDIS_USERNAME"], f"{service}_user")
                self.assertEqual(values["REDIS_DB"], "0")
                self.assertEqual(values["REDIS_HOST"], "redis")
                passwords.add(values["REDIS_PASSWORD"])
            self.assertEqual(len(passwords), 5)
            for name in ("users.acl", "monitor_password"):
                self.assertEqual(
                    stat.S_IMODE((destination / name).stat().st_mode), 0o444
                )
            self.assertEqual(
                stat.S_IMODE((destination / "operator.env").stat().st_mode), 0o600
            )

    def test_custom_redis_host_is_used_for_service_envs(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "secrets"
            generate_credentials(destination, host="studio-redis")
            for service in SERVICES:
                values = dict(
                    line.split("=", 1)
                    for line in (destination / f"{service}.env").read_text().splitlines()
                )
                self.assertEqual(values["REDIS_HOST"], "studio-redis")

    def test_existing_credentials_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "secrets"
            generate_credentials(destination)
            original = {path.name: path.read_bytes() for path in destination.iterdir()}
            with self.assertRaises(FileExistsError):
                generate_credentials(destination)
            self.assertEqual(
                original,
                {path.name: path.read_bytes() for path in destination.iterdir()},
            )


if __name__ == "__main__":
    unittest.main()
