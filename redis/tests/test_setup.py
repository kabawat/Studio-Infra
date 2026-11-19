"""Exercise the root setup flow with temporary files and a fake Docker CLI."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INFRA_DIRECTORY = Path(__file__).resolve().parents[2]
REDIS_DIRECTORY = INFRA_DIRECTORY / "redis"
SERVICE_DIRS = {
    "auth": "studio-auth",
    "core": "studio-core",
    "payment": "studio-payment",
    "utility": "studio-utility",
    "config": "config",
}


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.infra = self.root / "Infra"
        self.infra.mkdir()
        shutil.copy2(INFRA_DIRECTORY / "setup.sh", self.infra / "setup.sh")
        shutil.copytree(REDIS_DIRECTORY / "scripts", self.infra / "redis" / "scripts")
        (self.infra / "redis" / "docker-compose.dev.yml").touch()
        (self.infra / "redis" / "docker-compose.yml").touch()

        for dirname in SERVICE_DIRS.values():
            directory = self.root / dirname
            directory.mkdir()
            (directory / ".env").write_text(
                "NODE_ENV=development\nREDIS_HOST=old-host\nREDIS_KEY_PREFIX=old:\nKEEP_SETTING=keep\n"
            )

        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.trace = self.root / "docker-calls"
        fake_docker = self.fake_bin / "docker"
        fake_docker.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "with open(os.environ['DOCKER_TRACE'], 'a') as trace:\n"
            "    trace.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(0 if sys.argv[1:2] in (['info'], ['compose']) else 2)\n"
        )
        fake_docker.chmod(0o755)
        self.dragbizz_dir = self.root / "etc-dragbizz"
        self.environment = {
            **os.environ,
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "DOCKER_TRACE": str(self.trace),
            "DRAGBIZZ_DIR": str(self.dragbizz_dir),
        }

    def run_setup(self, *arguments, input_text=None):
        return subprocess.run(
            ["bash", str(self.infra / "setup.sh"), *arguments],
            input=input_text,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_yes_mode_generates_syncs_and_reuses_credentials(self):
        result = self.run_setup("-y")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("REDIS_PASSWORD", result.stdout + result.stderr)

        secrets = self.infra / "redis" / "secrets"
        expected = {
            "users.acl",
            "monitor_password",
            "operator.env",
            "auth.env",
            "core.env",
            "payment.env",
            "utility.env",
            "config.env",
        }
        self.assertEqual({path.name for path in secrets.iterdir()}, expected)
        original_acl = (secrets / "users.acl").read_bytes()

        for service, dirname in SERVICE_DIRS.items():
            environment = dict(
                line.split("=", 1)
                for line in (self.root / dirname / ".env").read_text().splitlines()
            )
            self.assertEqual(environment["REDIS_HOST"], "redis")
            self.assertEqual(environment["REDIS_USERNAME"], f"{service}_user")
            self.assertEqual(environment["KEEP_SETTING"], "keep")
            self.assertNotIn("REDIS_KEY_PREFIX", environment)
            self.assertEqual(
                (self.root / dirname / ".env").stat().st_mode & 0o777,
                0o600,
            )

        rerun = self.run_setup("-y")
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertEqual((secrets / "users.acl").read_bytes(), original_acl)
        self.assertIn("compose", self.trace.read_text())

    def test_manual_mode_uses_requested_host(self):
        result = self.run_setup(input_text="redis.test.internal\ny\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        auth_settings = dict(
            line.split("=", 1)
            for line in (self.root / "studio-auth" / ".env").read_text().splitlines()
        )
        self.assertEqual(auth_settings["REDIS_HOST"], "redis.test.internal")

    def test_incomplete_secrets_fail_without_overwrite_or_start(self):
        secrets = self.infra / "redis" / "secrets"
        secrets.mkdir()
        acl_path = secrets / "users.acl"
        acl_path.write_text("existing-acl-data\n")

        result = self.run_setup("-y")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete", result.stderr)
        self.assertEqual(acl_path.read_text(), "existing-acl-data\n")
        self.assertNotIn("compose", self.trace.read_text())

    def test_production_mode_uses_production_compose(self):
        result = self.run_setup("--prod", "-y")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("REDIS_PASSWORD", result.stdout + result.stderr)

        docker_calls = self.trace.read_text()
        self.assertIn("docker-compose.yml", docker_calls)
        self.assertNotIn("docker-compose.dev.yml", docker_calls)


if __name__ == "__main__":
    unittest.main()
