'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadConnection, loadServiceModule } = require('./support/services');

for (const service of ['core', 'auth', 'utility', 'finance']) {
    test(`${service}: connect, health, tracked subscribers and graceful disconnect`, async () => {
        const errors = [];
        const logger = {
            error: (message, details) => errors.push({ message, details }),
            warn: (message, details) => errors.push({ message, details }),
        };
        const config = loadConnection(service, logger);

        try {
            await Promise.all([config.connectRedis(), config.connectRedis()]);
            assert.equal((await config.checkRedisHealth()).status, 'healthy');
            await config.redisClient.set('infra-test:config', service, { EX: 60 });
            assert.equal(await config.redisClient.get('infra-test:config'), service);
            await config.redisClient.del('infra-test:config');

            const subscriber = config.createRedisClient();
            await subscriber.connect();
            assert.equal(await subscriber.ping(), 'PONG');
        } finally {
            await config.disconnectRedis();
        }

        assert.equal(config.redisClient.isOpen, false);
        assert.equal((await config.checkRedisHealth()).status, 'unhealthy');
        await assert.rejects(config.redisClient.set('offline-test', 'must-fail'), /closed/i);
        assert.deepEqual(errors, []);
    });
}

test('Core cache invalidation preserves a different store', async () => {
    const logger = { error: console.error, warn: console.warn };
    const config = loadConnection('core', logger);
    const cache = loadServiceModule('core', 'src/services/cache.service.js', {
        '@src/config/redis.config': config,
        '@src/utils/logger': logger,
    });
    try {
        await config.connectRedis();
        assert.equal(await cache.setCache('infra-cache:store-a:1', { id: 'a' }, 60), true);
        assert.equal(await cache.setCache('infra-cache:store-b:1', { id: 'b' }, 60), true);
        assert.equal(await cache.clearCachePattern('infra-cache:store-a:*'), 1);
        assert.equal(await cache.getCache('infra-cache:store-a:1'), null);
        assert.deepEqual(await cache.getCache('infra-cache:store-b:1'), { id: 'b' });
    } finally {
        if (config.redisClient.isReady) await cache.clearCachePattern('infra-cache:*');
        await config.disconnectRedis();
    }
});
