'use strict';

const assert = require('node:assert/strict');
const { describe, before, after, test } = require('node:test');
const { createFixture, scopedClient, waitUntil, delay } = require('../support/redis');

const SYNC_CHANNEL = 'core:sync:changes';
const DOMAIN_STREAM = 'core:stream:events:domain';
const REPLAY_MAILBOX = 'core:ai:events:replay_request';
const RATE_LIMIT_SCRIPT = `
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count`;

describe('Coordination and shared service contracts (staging only)', () => {
    const fixture = createFixture();
    const clients = {};

    before(async () => {
        for (const service of ['core', 'auth', 'utility', 'brain']) {
            clients[service] = await fixture.connect(service);
        }
    });
    after(async () => {
        await fixture.close();
        assert.deepEqual(fixture.errors, []);
    });

    test('100 concurrent rate increments retain one expiry window', async () => {
        const { scoped } = clients.auth;
        const key = `${fixture.prefix}:rate`;
        const counts = await Promise.all(Array.from({ length: 100 }, () =>
            scoped.eval(RATE_LIMIT_SCRIPT, { keys: [key], arguments: ['60'] })));
        assert.equal(new Set(counts).size, 100);
        assert.equal(await scoped.get(key), '100');
        assert(await scoped.ttl(key) > 0);
    });

    test('temporary OTP expires and lock acquisition is exclusive', async () => {
        const key = `${fixture.prefix}:otp`;
        await clients.auth.scoped.set(key, 'otp', { PX: 30 });
        await delay(60);
        assert.equal(await clients.auth.scoped.get(key), null);

        const lock = `${fixture.prefix}:lock`;
        assert.equal(await clients.brain.scoped.set(lock, 'owner', { NX: true, EX: 60 }), 'OK');
        assert.equal(await clients.brain.scoped.set(lock, 'other', { NX: true, EX: 60 }), null);
    });

    test('Core publishes sync changes; Utility only subscribes', async () => {
        const subscriber = (await fixture.connect('utility')).raw;
        let message;
        try {
            await subscriber.subscribe(SYNC_CHANNEL, value => { message = value; });
            await clients.core.raw.publish(SYNC_CHANNEL, fixture.prefix);
            await waitUntil(() => message === fixture.prefix);
            await assert.rejects(clients.utility.raw.publish(SYNC_CHANNEL, 'bad'), /NOPERM/);
            await assert.rejects(clients.auth.raw.subscribe(SYNC_CHANNEL, () => {}), /NOPERM/);
        } finally {
            await subscriber.unsubscribe();
        }
    });

    test('Brain consumes Core events without deleting or adding stream entries', async () => {
        const core = clients.core.raw;
        const brain = clients.brain.raw;
        const group = fixture.prefix;
        const eventId = await core.xAdd(DOMAIN_STREAM, '*', { eventId: group, storeId: 'tenant-a' });
        await brain.xGroupCreate(DOMAIN_STREAM, group, '0');
        try {
            const streams = await brain.xReadGroup(group, 'consumer', {
                key: DOMAIN_STREAM, id: '>',
            }, { COUNT: 1 });
            assert(streams.length > 0);
            await brain.xAck(DOMAIN_STREAM, group, streams[0].messages[0].id);
            await assert.rejects(brain.del(DOMAIN_STREAM), /NOPERM/);
            await assert.rejects(brain.xAdd(DOMAIN_STREAM, '*', { bad: 'bad' }), /NOPERM/);
        } finally {
            await core.xGroupDestroy(DOMAIN_STREAM, group);
            await core.xDel(DOMAIN_STREAM, eventId);
        }
    });

    test('Brain and Core share only the replay mailbox', async () => {
        assert.equal(await clients.core.raw.exists(REPLAY_MAILBOX), 0,
            'Use a clean staging Redis; do not overwrite an existing replay request');
        try {
            await clients.brain.raw.set(REPLAY_MAILBOX, fixture.prefix, { EX: 60 });
            assert.equal(await clients.core.scoped.get('ai:events:replay_request'), fixture.prefix);
        } finally {
            await clients.core.raw.del(REPLAY_MAILBOX);
        }
    });

    test('response deadlines reject a blocked Redis command', async () => {
        const timed = scopedClient(clients.core.raw, 'core', { commandTimeout: 30 });
        await assert.rejects(timed.blPop(`${fixture.prefix}:deadline`, 1), /timed out/i);
    });

    test('sample local write latency', async context => {
        const latencies = [];
        for (let index = 0; index < 100; index++) {
            const start = performance.now();
            await clients.core.scoped.set(`${fixture.prefix}:latency`, String(index), { EX: 60 });
            latencies.push(performance.now() - start);
        }
        latencies.sort((first, second) => first - second);
        assert.equal(await clients.core.scoped.get(`${fixture.prefix}:latency`), '99');
        context.diagnostic(JSON.stringify({ writeLatencyMs: {
            p50: latencies[50], p95: latencies[95], p99: latencies[99],
        } }));
    });
});
