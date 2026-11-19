'use strict';

// Node's built-in test runner reports each contract and handles failures independently.
// Run only against staging: shared event/replay contract tests touch their exact ACL keys.
require('./integration/isolation.test');
require('./integration/coordination.test');
require('./integration/queues.test');
