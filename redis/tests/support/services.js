'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { compileFunction } = require('node:vm');

const REDIS_DIRECTORY = path.resolve(__dirname, '../..');
const BACKEND_DIRECTORY = path.resolve(REDIS_DIRECTORY, '../..');
const SERVICE_NAMES = ['core', 'auth', 'utility', 'payment', 'config'];

const SERVICE_DIRECTORIES = {
    core: 'studio-core',
    auth: 'studio-auth',
    utility: 'studio-utility',
    payment: 'studio-payment',
    config: 'config',
};

function serviceDirectory(service) {
    return path.join(BACKEND_DIRECTORY, SERVICE_DIRECTORIES[service] || service);
}

function serviceRequire(service) {
    return createRequire(path.join(serviceDirectory(service), 'package.json'));
}

function serviceEnvironment(service) {
    const secretsDirectory =
        process.env.REDIS_TEST_SECRETS_DIRECTORY ||
        path.join(REDIS_DIRECTORY, 'secrets');
    const contents = fs.readFileSync(path.join(secretsDirectory, `${service}.env`), 'utf8');
    const environment = {};

    for (const line of contents.split(/\r?\n/)) {
        if (!line.trim() || line.trimStart().startsWith('#')) {
            continue;
        }
        const separator = line.indexOf('=');
        if (separator < 1) {
            throw new Error(`Invalid environment entry for ${service}`);
        }
        environment[line.slice(0, separator)] = line.slice(separator + 1);
    }
    if (process.env.REDIS_TEST_HOST) {
        environment.REDIS_HOST = process.env.REDIS_TEST_HOST;
    }
    return environment;
}

function loadServiceModule(service, relativePath, dependencies = {}, environment = {}) {
    const filename = path.join(serviceDirectory(service), relativePath);
    const requireFromService = createRequire(filename);
    const module = { exports: {} };
    const requireDependency = name => {
        if (Object.hasOwn(dependencies, name)) {
            return dependencies[name];
        }
        return requireFromService(name);
    };
    const execute = compileFunction(fs.readFileSync(filename, 'utf8'), [
        'require', 'module', 'exports', '__filename', '__dirname', 'process',
    ], { filename });

    // Inject dependencies into this module only; never modify Node's global loader.
    execute(requireDependency, module, module.exports, filename, path.dirname(filename), {
        env: { ...environment },
    });
    return module.exports;
}

function loadConnection(service, logger) {
    return loadServiceModule(service, 'src/config/redis.config.js', {
        './env.config': { redis: {} },
        '@src/utils/logger': logger,
    }, serviceEnvironment(service));
}

module.exports = {
    REDIS_DIRECTORY,
    SERVICE_NAMES,
    serviceRequire,
    serviceEnvironment,
    loadServiceModule,
    loadConnection,
};
