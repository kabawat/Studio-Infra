"""Integration contracts for Brain's actual Redis stores. Requires a staging Redis."""

import asyncio
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

REDIS_DIRECTORY = Path(__file__).resolve().parents[1]
BACKEND_DIRECTORY = REDIS_DIRECTORY.parents[1]
sys.path.insert(0, str(BACKEND_DIRECTORY / "studio-Brain"))

from app.confirmations.redis_store import RedisConfirmationStore
from app.core.exceptions import AppError
from app.idempotency.store import RedisIdempotencyStore, build_idempotency_key
from app.insights.cache import RedisInsightCache
from app.limits.concurrency import ConcurrencyGate
from app.schemas.tool import ToolResult
from redis.asyncio import Redis


def redis_settings() -> dict[str, str]:
    secrets_directory = Path(
        os.environ.get("REDIS_TEST_SECRETS_DIRECTORY", REDIS_DIRECTORY / "secrets")
    )
    return dict(
        line.split("=", 1)
        for line in (secrets_directory / "brain.env").read_text().splitlines()
        if line and not line.startswith("#")
    )


class BrainRedisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        settings = redis_settings()
        self.client = Redis(
            host=os.environ.get("REDIS_TEST_HOST", settings["REDIS_HOST"]),
            username=settings["REDIS_USERNAME"],
            password=settings["REDIS_PASSWORD"],
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            max_connections=32,
        )
        self.addAsyncCleanup(self.client.aclose)
        self.cleanup_keys = []
        self.addAsyncCleanup(self.delete_test_keys)
        self.assertTrue(await self.client.ping())
        self.user_id = f"infra-test-{uuid4().hex}"
        self.session_id = f"infra-test-{uuid4().hex}"
        self.store_a, self.store_b = uuid4().hex[:24], uuid4().hex[:24]

    async def delete_test_keys(self):
        if self.cleanup_keys:
            await self.client.delete(*self.cleanup_keys)

    async def test_tenant_leases_and_owner_checked_release(self):
        gate = ConcurrencyGate(self.client, max_chat=1)
        owner = {"user_id": self.user_id, "store_id": self.store_a}
        lease = await gate.acquire_chat(**owner)
        self.addAsyncCleanup(gate.release, lease)
        with self.assertRaises(AppError):
            await gate.acquire_chat(**owner)

        await gate.release(replace(lease, token="wrong-owner"))
        with self.assertRaises(AppError):
            await gate.acquire_chat(**owner)

        other_store_lease = await gate.acquire_chat(
            user_id=self.user_id, store_id=self.store_b
        )
        self.addAsyncCleanup(gate.release, other_store_lease)

    async def test_confirmation_owner_and_atomic_claim(self):
        store = RedisConfirmationStore(self.client, ttl_seconds=60)
        owner = {
            "user_id": self.user_id,
            "store_id": self.store_a,
            "session_id": self.session_id,
        }
        record = await store.create(
            **owner,
            type="create_expense",
            draft_id="infra-test-draft",
            summary={},
            tool_name="create_expense",
            arguments={"amount": 1},
        )
        self.cleanup_keys.append(f"brain:ai:confirmation:{record.confirmation_id}")
        with self.assertRaises(AppError):
            await store.get(
                record.confirmation_id, **{**owner, "store_id": self.store_b}
            )

        claims = await asyncio.gather(
            *[store.claim(record.confirmation_id, **owner) for _ in range(10)],
            return_exceptions=True,
        )
        successful = [
            result for result in claims if not isinstance(result, BaseException)
        ]
        failed = [result for result in claims if isinstance(result, BaseException)]
        self.assertEqual(len(successful), 1)
        self.assertTrue(all(isinstance(error, AppError) for error in failed))

    async def test_cache_invalidation_is_store_scoped(self):
        cache = RedisInsightCache(self.client, ttl_seconds=60)
        for store in (self.store_a, self.store_b):
            self.cleanup_keys.extend(
                [
                    f"brain:ai:insight-index:{store}",
                    f"brain:ai:insight:{store}:sales:week",
                ]
            )
            await cache.set(store, "sales", "week", {"store": store})

        self.assertEqual(await cache.invalidate_store(self.store_a), 1)
        self.assertIsNone(await cache.get(self.store_a, "sales", "week"))
        self.assertEqual(
            await cache.get(self.store_b, "sales", "week"), {"store": self.store_b}
        )

    async def test_storage_namespace_preserves_public_idempotency_token(self):
        token = build_idempotency_key(
            session_id=self.session_id, tool_name="example", arguments={}
        )
        self.assertTrue(token.startswith("ai:idempotency:"))
        storage_key = f"brain:{token}"
        self.cleanup_keys.append(storage_key)
        store = RedisIdempotencyStore(self.client, ttl_seconds=60)
        await store.put(
            token, ToolResult(tool="example", success=True, data={"ok": True})
        )
        self.assertTrue((await store.get(token)).success)
        self.assertTrue(await self.client.exists(storage_key))


if __name__ == "__main__":
    unittest.main(verbosity=2)
