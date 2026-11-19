"""Exercise operational scripts with fake Docker/Redis commands, never real data."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIRECTORY = Path(__file__).resolve().parents[1] / "scripts"
FAKE_COMMAND = """#!/usr/bin/env python3
import json, os, pathlib, sys
arguments = sys.argv[1:]
with open(os.environ['TEST_TRACE'], 'a') as output:
    output.write(json.dumps(arguments) + '\\n')
if arguments[:2] == ['exec', '-i']:
    password = sys.stdin.read().strip()
    if not password:
        sys.exit(1)
if 'redis-check-rdb' in arguments and os.environ.get('TEST_FAIL_CHECK') == '1':
    sys.exit(1)
if arguments and arguments[0] == 'cp':
    pathlib.Path(arguments[-1]).write_bytes(b'test-snapshot')
"""


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.redis = self.directory / "redis"
        shutil.copytree(SCRIPTS_DIRECTORY, self.redis / "scripts")
        secrets = self.redis / "secrets"
        secrets.mkdir()
        (secrets / "operator.env").write_text("REDISCLI_AUTH=test-password\n")
        (secrets / "monitor_password").write_text("test-password")
        for name in ("docker", "redis-server"):
            command = self.directory / name
            command.write_text(FAKE_COMMAND)
            command.chmod(0o755)
        self.trace = self.directory / "trace.jsonl"
        self.environment = {
            **os.environ,
            "PATH": f"{self.directory}:{os.environ['PATH']}",
            "TEST_TRACE": str(self.trace),
        }

    def run_script(self, name, *arguments, **environment):
        self.trace.write_text("")
        result = subprocess.run(
            ["sh", str(self.redis / "scripts" / name), *arguments],
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
            check=False,
        )
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return result, calls

    def test_backup_validates_and_checksums_snapshot_then_cleans_remote_file(self):
        destination = self.directory / "backup with spaces"
        result, calls = self.run_script("backup.sh", str(destination))
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = next(destination.glob("*.rdb"))
        self.assertTrue(Path(f"{snapshot}.sha256").exists())
        self.assertIn("redis-check-rdb", calls[1])
        self.assertEqual(calls[-1][:4], ["exec", "redis", "rm", "-f"])
        self.assertNotIn("test-password", json.dumps(calls))
        self.assertNotIn("test-password", result.stdout + result.stderr)

    def test_failed_snapshot_validation_aborts_copy_but_still_cleans_remote_file(self):
        result, calls = self.run_script("backup.sh", TEST_FAIL_CHECK="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] == "cp" for call in calls))
        self.assertEqual(calls[-1][:4], ["exec", "redis", "rm", "-f"])

    def test_monitor_uses_read_only_identity_and_configured_container(self):
        result, calls = self.run_script("monitor.sh", REDIS_CONTAINER="redis-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0][:3], ["exec", "-i", "redis-test"])
        self.assertIn("monitor_user", calls[0])
        self.assertEqual(
            calls[0][-5:], ["INFO", "memory", "clients", "persistence", "stats"]
        )

    def test_start_rejects_invalid_memory_before_launching_redis(self):
        for memory in ("0", "mb", "1mm", "-1", "256mb extra"):
            with self.subTest(memory=memory):
                result, calls = self.run_script("start.sh", REDIS_MAXMEMORY=memory)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
        result, calls = self.run_script("start.sh", REDIS_MAXMEMORY="256mb")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0][-2:], ["--maxmemory", "256mb"])


if __name__ == "__main__":
    unittest.main()
