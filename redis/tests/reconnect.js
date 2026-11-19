'use strict';

const assert = require('node:assert/strict');
const { setTimeout: delay } = require('node:timers/promises');
const { loadConnection } = require('./support/services');

const PERSISTENCE_KEY = 'core:infra-restart-persistence';
const RECOVERY_TIMEOUT_MS = 45000;
const errors = [];
const config = loadConnection('core', {
    error: (message, details) => errors.push({ message, details }),
    warn: (message, details) => errors.push({ message, details }),
});

async function verifyRecovery() {
    const client = config.redisClient;
    await config.connectRedis();
    await client.set(PERSISTENCE_KEY, 'durable', { EX: 3600 });
    let disconnected = false;
    client.on('reconnecting', () => { disconnected = true; });
    console.log('READY_FOR_RESTART');

    const deadline = Date.now() + RECOVERY_TIMEOUT_MS;
    while (Date.now() < deadline) {
        if (!client.isReady) {
            disconnected = true;
            await assert.rejects(client.get(PERSISTENCE_KEY));
        } else if (disconnected) {
            assert.equal(await client.get(PERSISTENCE_KEY), 'durable');
            console.log(`PASS: outage rejected commands and recovery preserved data (${errors.length} outage logs).`);
            return;
        }
        await delay(50);
    }
    throw new Error('Redis did not disconnect and recover within the test window');
}

verifyRecovery()
    .catch(error => { console.error(error); process.exitCode = 1; })
    .finally(() => config.disconnectRedis());
