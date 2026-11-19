'use strict';

const assert = require('node:assert/strict');
const { describe, before, after, test } = require('node:test');
const { SERVICE_NAMES } = require('../support/services');
const { createFixture } = require('../support/redis');

const FORBIDDEN_COMMANDS = [
    ['CONFIG', 'SET', 'maxmemory', '0'],
    ['FLUSHALL'], ['SCAN', '0'], ['KEYS', '*'], ['SELECT', '1'], ['ACL', 'LIST'],
];

describe('Service namespaces and ACLs', () => {
    const fixture = createFixture();
    const clients = {};

    before(async () => {
        for (const service of SERVICE_NAMES) clients[service] = await fixture.connect(service);
    });
    after(async () => {
        await fixture.close();
        assert.deepEqual(fixture.errors, []);
    });

    for (const service of SERVICE_NAMES) {
        test(`${service}: connection, tenant keys, TTL and transactions`, async () => {
            const { raw, scoped } = clients[service];
            const firstStore = `${fixture.prefix}:store:a`;
            const secondStore = `${fixture.prefix}:store:b`;

            assert.equal(await raw.ping(), 'PONG');
            await scoped.set(firstStore, 'a', { EX: 60 });
            await scoped.set(secondStore, 'b', { EX: 60 });
            assert.equal(await scoped.get(firstStore), 'a');
            assert.equal(await scoped.get(secondStore), 'b');
            assert(await scoped.ttl(firstStore) > 0);

            const transaction = scoped.multi()
                .set(`${fixture.prefix}:multi`, 'ok', { EX: 60 })
                .get(`${fixture.prefix}:multi`);
            assert.deepEqual(await transaction.exec(), ['OK', 'ok']);
        });

        test(`${service}: foreign keys and administrative commands are denied`, async () => {
            const { raw } = clients[service];
            for (const other of SERVICE_NAMES.filter(name => name !== service)) {
                const key = `${other}:${fixture.prefix}:store:a`;
                await assert.rejects(raw.get(key), /NOPERM/);
                await assert.rejects(raw.set(key, 'bad'), /NOPERM/);
            }
            for (const command of FORBIDDEN_COMMANDS) {
                await assert.rejects(raw.sendCommand(command), /NOPERM/);
            }
            await assert.rejects(raw.eval("return redis.call('GET', KEYS[1])", {
                keys: [`foreign:${fixture.prefix}`], arguments: [],
            }), /NOPERM/);
        });
    }
});
