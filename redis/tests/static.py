"""Infrastructure/service wiring checks; no Docker or production credentials needed."""

import sys
import unittest
from pathlib import Path

import yaml

INFRA = Path(__file__).resolve().parents[1]
ROOT = INFRA.parents[1]
sys.path.insert(0, str(INFRA / "scripts"))
from redis_setup.acl import IDENTITIES, acl_lines

SERVICE_DIRECTORIES = {
    "auth": "studio-auth",
    "core": "studio-core",
    "payment": "studio-payment",
    "utility": "studio-utility",
    "config": "studio-config",
}

SCOPE_PATHS = {
    "auth": "studio-auth/src/db/redis.scope.js",
    "core": "studio-core/src/infrastructure/cache/redis/scope.js",
    "payment": "studio-payment/src/config/redis.scope.js",
    "utility": "studio-utility/src/infrastructure/queue/scope.js",
    "config": "studio-config/src/infrastructure/cache/redis/scope.js",
}


class ConfigurationTests(unittest.TestCase):
    def test_one_server_and_private_network(self):
        cfg = yaml.safe_load((INFRA / "compose.yml").read_text())
        self.assertEqual(list(cfg["services"]), ["redis"])
        redis = cfg["services"]["redis"]
        self.assertNotIn("ports", redis)
        self.assertTrue(cfg["networks"]["redis-private"]["internal"])
        self.assertIn("noeviction", (INFRA / "redis.conf").read_text())
        self.assertIn("appendfsync always", (INFRA / "redis.conf").read_text())

    def test_five_services_use_same_server(self):
        for service, directory in SERVICE_DIRECTORIES.items():
            compose_files = list((ROOT / directory).glob("docker-compose*.yml"))
            self.assertTrue(compose_files, f"Missing compose files for {directory}")
            for f in compose_files:
                cfg = yaml.safe_load(f.read_text())
                self.assertFalse(any(s.startswith("redis") for s in cfg["services"]))
                self.assertTrue(cfg["networks"]["redis-private"]["external"])
                for app in cfg["services"].values():
                    self.assertIn("redis-private", app["networks"])
                    self.assertIn("default", app["networks"])

    def test_scoped_helpers_remain_identical(self):
        helpers = [(ROOT / path).read_text() for path in SCOPE_PATHS.values()]
        self.assertTrue(all(h == helpers[0] for h in helpers))

    def test_acl_generation_contains_no_cleartext_secrets(self):
        passwords = {s: f"unit-test-{s}-password" for s in IDENTITIES}
        acl = "\n".join(acl_lines(passwords))
        for password in passwords.values():
            self.assertNotIn(password, acl)
        self.assertIn("user default off", acl)
        self.assertIn("-scan", acl)
        for service in SERVICE_DIRECTORIES:
            self.assertIn(f"user {service}_user on #", acl)
            self.assertIn(f"~{service}:*", acl)


if __name__ == "__main__":
    unittest.main()
