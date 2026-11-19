'use strict';

const { createFixture } = require('./support/redis');

const PERSISTENCE_KEY = 'core:infra-restart-persistence';
const STREAM = 'core:infra-restore-stream';
const GROUP = 'infra-restore-group';

async function createRecoveryFixture() {
    const fixture = createFixture();
    try {
        const { raw: client } = await fixture.connect('core');
        await client.set(PERSISTENCE_KEY, 'durable', { EX: 3600 });
        if (!await client.exists(STREAM)) {
            await client.xAdd(STREAM, '*', {
                eventId: 'restore-pending-event', storeId: 'infra-test',
            });
            await client.xGroupCreate(STREAM, GROUP, '0');
            await client.xReadGroup(GROUP, 'infra-consumer', {
                key: STREAM, id: '>',
            }, { COUNT: 1 });
            await client.expire(STREAM, 3600);
        }
        console.log('Created persistence and pending-stream recovery fixtures.');
    } finally {
        await fixture.close();
    }
}

createRecoveryFixture().catch(error => { console.error(error); process.exitCode = 1; });
