const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

test('operations toggle persists separately and malformed settings disable only assistance', async () => {
  const { createOperationsSettings } = await import('../lib/operations-settings.js');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-operations-settings-'));
  try {
    const settings = createOperationsSettings({ stateDir: root });
    assert.deepEqual(settings.read(), { enabled: true });
    assert.deepEqual(settings.save({ enabled: false }), { enabled: false });
    assert.deepEqual(createOperationsSettings({ stateDir: root }).read(), { enabled: false });
    assert.throws(() => settings.save({ enabled: 'true' }));
    fs.writeFileSync(path.join(root, 'operations.json'), '{broken');
    assert.equal(settings.read().enabled, false);
    assert.match(settings.read().error, /原有录题/);
  } finally {
    assert.ok(root.startsWith(path.join(os.tmpdir(), 'wf-operations-settings-')));
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('operations model uses explicit role or configured default, never an invalid-role fallback', async () => {
  const { operationsRoute } = await import('../lib/operations-settings.js');
  assert.deepEqual(operationsRoute({ default: { provider: 'p', model: 'default' } }), { provider: 'p', model: 'default' });
  assert.deepEqual(operationsRoute({ default: { provider: 'p', model: 'default' }, roles: { operations: { provider: 'q', model: 'ops' } } }), { provider: 'q', model: 'ops' });
  assert.throws(() => operationsRoute({ default: { provider: 'p', model: 'default' }, roles: { operations: {} } }));
  assert.throws(() => operationsRoute({}));
});
