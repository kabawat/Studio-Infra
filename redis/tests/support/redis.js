'use strict';

const { randomUUID } = require('node:crypto');
const { setTimeout: delay } = require('node:timers/promises');
const { serviceRequire, serviceEnvironment } = require('./services');

const { createClient } = serviceRequire('core')('redis');
const { scopedClient } = serviceRequire('core')('./src/config/redis.scope');

function queueConnection(service) {
    const environment = serviceEnvironment(service);
    return {
        host: environment.REDIS_HOST,
        port: Number(environment.REDIS_PORT || 6379),
        username: environment.REDIS_USERNAME,
        password: environment.REDIS_PASSWORD,
        maxRetriesPerRequest: null,
    };
}

function createFixture() {
    const clients = [];
    const errors = [];
    const prefix = `infra-test-${randomUUID()}`;

    async function connect(service) {
        const connection = queueConnection(service);
        const client = createClient({
            socket: {
                host: connection.host,
                port: connection.port,
                connectTimeout: 5000,
                reconnectStrategy: false,
            },
            username: connection.username,
            password: connection.password,
            database: 0,
        });
        clients.push(client);
        client.on('error', error => errors.push(error));
        await client.connect();
        return { raw: client, scoped: scopedClient(client, service) };
    }

    async function close() {
        const results = await Promise.allSettled(
            clients.map(async client => {
                if (!client.isOpen) return;
                if (client.isReady) {
                    await client.quit();
                } else {
                    client.destroy();
                }
            }),
        );
        const failures = results.filter(result => result.status === 'rejected');
        if (failures.length) {
            throw new AggregateError(
                failures.map(result => result.reason),
                'Failed to close one or more Redis test clients',
            );
        }
    }

    return { prefix, errors, connect, close };
}

async function waitUntil(predicate, { timeout = 2000, interval = 10 } = {}) {
    const expiresAt = Date.now() + timeout;
    while (!predicate()) {
        if (Date.now() >= expiresAt) {
            throw new Error('Timed out waiting for Redis delivery');
        }
        await delay(interval);
    }
}

module.exports = { createFixture, queueConnection, scopedClient, waitUntil, delay };
