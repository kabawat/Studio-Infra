'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadServiceModule } = require('./support/services');

const offlineClient = {
    isOpen: false,
    isReady: false,
    async eval() { throw new Error('Redis unavailable'); },
};

for (const service of ['core', 'auth']) {
    test(`${service}: rate limiting rejects Redis unavailability`, async () => {
        const { rateLimit } = loadServiceModule(service, 'src/utils/helper/rateLimit.helper.js', {
            '@src/config/redis.config': { redisClient: offlineClient },
        });
        await assert.rejects(rateLimit('test', 10, 60), /unavailable/);
    });
}

test('Finance returns 503 without forwarding a request while Redis is down', async () => {
    const createLimiter = loadServiceModule('finance', 'src/middlewares/rateLimit.middleware.js', {
        '@src/config/redis.config': { redisClient: offlineClient },
        '@src/utils/logger': { warn() {} },
        '@src/utils/responseHandler/standardized': {
            error(response, details) { response.statusCode = details.statusCode; },
        },
    });
    const response = {};
    let forwarded = false;
    await createLimiter()({ ip: 'test' }, response, () => { forwarded = true; });
    assert.equal(response.statusCode, 503);
    assert.equal(forwarded, false);
});
