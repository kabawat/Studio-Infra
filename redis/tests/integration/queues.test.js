'use strict';

const assert = require('node:assert/strict');
const { once } = require('node:events');
const { test } = require('node:test');
const { randomUUID } = require('node:crypto');
const { serviceRequire } = require('../support/services');
const { queueConnection } = require('../support/redis');

for (const service of ['core', 'utility']) {
    test(`${service}: BullMQ executes a job with the service namespace`, async () => {
        const { Queue, Worker } = serviceRequire(service)('./src/config/redis.queues');
        const name = `infra-test-${randomUUID()}`;
        const connection = queueConnection(service);
        const prefix = `${service}:bull`;
        const queue = new Queue(name, { prefix, connection });
        const worker = new Worker(name, async job => ({ store: job.data.storeId }), {
            prefix, connection,
        });
        const errors = [];
        queue.on('error', error => errors.push(error));
        worker.on('error', error => errors.push(error));

        try {
            await worker.waitUntilReady();
            const completion = once(worker, 'completed', { signal: AbortSignal.timeout(10000) });
            const [job] = await Promise.all([completion, queue.add('tenant-job', { storeId: 'tenant-a' })]);
            assert.equal(job[0].returnvalue.store, 'tenant-a');
            assert.deepEqual(errors, []);
        } finally {
            // This queue has a unique test name; application queues are never removed.
            await worker.close();
            try {
                await queue.obliterate({ force: true });
            } finally {
                await queue.close();
            }
        }
    });
}

test('Auth: Bull executes a notification job with the auth namespace', async () => {
    const { Queue } = serviceRequire('auth')('./src/config/redis.queues');
    const queue = new Queue(`infra-test-${randomUUID()}`, {
        prefix: 'auth:bull', redis: queueConnection('auth'),
    });
    const errors = [];
    queue.on('error', error => errors.push(error));

    try {
        queue.process(async job => job.data);
        const completion = once(queue, 'completed', { signal: AbortSignal.timeout(10000) });
        const [completed] = await Promise.all([completion, queue.add({ storeId: 'tenant-a' })]);
        assert.equal(completed[0].returnvalue.storeId, 'tenant-a');
        assert.deepEqual(errors, []);
    } finally {
        try {
            await queue.obliterate({ force: true });
        } finally {
            await queue.close();
        }
    }
});
