const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, symlinkSync, renameSync, readdirSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, dirname } = require('node:path');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const moduleReady = import(pathToFileURL(resolve(__dirname, '../lib/code-restore.js')));

const sha = (text) => createHash('sha256').update(Buffer.from(text)).digest('hex');

function release() {
  const dir = mkdtempSync(join(tmpdir(), 'restore-'));
  const root = join(dir, 'plugin');
  const backup = join(dir, 'backup');
  mkdirSync(join(root, 'app/workflow'), { recursive: true });
  mkdirSync(backup, { recursive: true });
  return { dir, root, backup };
}

function journal(dir, body) {
  const path = join(dir, 'journal.json');
  writeFileSync(path, JSON.stringify(body, null, 1));
  return path;
}

test('restores replaced files and removes files the release added', async () => {
  const { restoreRelease } = await moduleReady;
  const { dir, root, backup } = release();
  writeFileSync(join(root, 'app/workflow/a.py'), 'new');
  writeFileSync(join(root, 'app/workflow/b.py'), 'added');
  writeFileSync(join(backup, 'a.py'), 'old');
  const path = journal(dir, {
    status: 'installed', source_root: root,
    changes: [
      { path: 'app/workflow/a.py', base_sha256: sha('old'), new_sha256: sha('new') },
      { path: 'app/workflow/b.py', base_sha256: 'missing', new_sha256: sha('added') },
    ],
    backups: { 'app/workflow/a.py': join(backup, 'a.py') },
  });
  const result = restoreRelease(path, { expectedRoot: root });
  assert.equal(result.ok, true);
  assert.deepEqual(result.restored.sort(), ['app/workflow/a.py', 'app/workflow/b.py']);
  assert.equal(readFileSync(join(root, 'app/workflow/a.py'), 'utf8'), 'old');
  assert.equal(existsSync(join(root, 'app/workflow/b.py')), false);
  assert.equal(JSON.parse(readFileSync(path, 'utf8')).status, 'rolled_back');
  rmSync(dir, { recursive: true, force: true });
});

test('refuses to overwrite a file that changed again after install', async () => {
  const { restoreRelease } = await moduleReady;
  const { dir, root, backup } = release();
  writeFileSync(join(root, 'app/workflow/a.py'), 'edited-by-someone-else');
  writeFileSync(join(backup, 'a.py'), 'old');
  const path = journal(dir, {
    status: 'installed', source_root: root,
    changes: [{ path: 'app/workflow/a.py', base_sha256: sha('old'), new_sha256: sha('new') }],
    backups: { 'app/workflow/a.py': join(backup, 'a.py') },
  });
  const result = restoreRelease(path, { expectedRoot: root });
  assert.equal(result.ok, false);
  assert.match(result.error, /source_changed_after_install/);
  assert.equal(readFileSync(join(root, 'app/workflow/a.py'), 'utf8'), 'edited-by-someone-else');
  assert.equal(JSON.parse(readFileSync(path, 'utf8')).status, 'installed');
  rmSync(dir, { recursive: true, force: true });
});

test('rejects a journal path that escapes the source root', async () => {
  const { restoreRelease } = await moduleReady;
  const { dir, root, backup } = release();
  writeFileSync(join(backup, 'evil.py'), 'old');
  const path = journal(dir, {
    status: 'installed', source_root: root,
    changes: [{ path: '../evil.py', base_sha256: sha('old'), new_sha256: sha('new') }],
    backups: { '../evil.py': join(backup, 'evil.py') },
  });
  const result = restoreRelease(path, { expectedRoot: root });
  assert.equal(result.ok, false);
  assert.match(result.error, /escapes_root/);
  rmSync(dir, { recursive: true, force: true });
});

function multiRelease(t) {
  const fixture = release();
  t.after(() => rmSync(fixture.dir, { recursive: true, force: true }));
  const changes = ['a', 'b', 'c'].map((name) => {
    const path = `app/workflow/${name}.py`;
    writeFileSync(join(fixture.root, path), 'new-' + name);
    writeFileSync(join(fixture.backup, name), 'old-' + name);
    return { path, base_sha256: sha('old-' + name), new_sha256: sha('new-' + name) };
  });
  const body = { status: 'installed', source_root: fixture.root, changes,
    backups: Object.fromEntries(changes.map((change, i) => [change.path, join(fixture.backup, ['a', 'b', 'c'][i])])) };
  return { ...fixture, body, path: journal(fixture.dir, body) };
}

for (const failure of ['missing', 'corrupt']) {
  test(`${failure} later backup leaves every installed file and journal unchanged`, async (t) => {
    const { restoreRelease } = await moduleReady;
    const f = multiRelease(t);
    if (failure === 'missing') rmSync(join(f.backup, 'c'));
    else writeFileSync(join(f.backup, 'c'), 'bad-backup');
    const before = readFileSync(f.path, 'utf8');
    const result = restoreRelease(f.path, { expectedRoot: f.root });
    assert.equal(result.ok, false);
    assert.match(result.error, /backup_missing|backup_corrupt/);
    for (const name of ['a', 'b', 'c']) assert.equal(readFileSync(join(f.root, `app/workflow/${name}.py`), 'utf8'), 'new-' + name);
    assert.equal(readFileSync(f.path, 'utf8'), before);
    assert.deepEqual(readdirSync(join(f.root, 'app/workflow')).sort(), ['a.py', 'b.py', 'c.py']);
  });
}

test('rejects a journal-selected root without touching it', async (t) => {
  const { restoreRelease } = await moduleReady;
  const f = multiRelease(t);
  assert.match(restoreRelease(f.path, { expectedRoot: f.backup }).error, /root_mismatch/);
  assert.match(restoreRelease(f.path).error, /expected_root_required/);
  assert.equal(readFileSync(join(f.root, 'app/workflow/a.py'), 'utf8'), 'new-a');
});

for (const component of ['target', 'parent', 'backup']) {
  test(`rejects ${component} symlink and leaves outside bytes unchanged`, async (t) => {
    const { restoreRelease } = await moduleReady;
    const f = multiRelease(t);
    const outside = join(f.dir, 'outside');
    mkdirSync(outside);
    writeFileSync(join(outside, 'a.py'), 'new-a');
    if (component === 'target') {
      rmSync(join(f.root, 'app/workflow/a.py'));
      symlinkSync(join(outside, 'a.py'), join(f.root, 'app/workflow/a.py'));
    } else if (component === 'parent') {
      renameSync(join(f.root, 'app/workflow'), join(outside, 'saved'));
      symlinkSync(outside, join(f.root, 'app/workflow'));
    } else {
      writeFileSync(join(outside, 'a.py'), 'old-a');
      rmSync(join(f.backup, 'a'));
      symlinkSync(join(outside, 'a.py'), join(f.backup, 'a'));
    }
    const before = readFileSync(join(outside, 'a.py'), 'utf8');
    const result = restoreRelease(f.path, { expectedRoot: f.root });
    assert.equal(result.ok, false);
    assert.match(result.error, /symlink_component/);
    assert.equal(readFileSync(join(outside, 'a.py'), 'utf8'), before);
    assert.equal(JSON.parse(readFileSync(f.path, 'utf8')).status, 'installed');
  });
}

test('failure after removing an added file puts it back with all installed bytes', async (t) => {
  const { restoreRelease } = await moduleReady;
  const f = multiRelease(t);
  f.body.changes[0].base_sha256 = 'missing';
  journal(f.dir, f.body);
  const before = readFileSync(f.path, 'utf8');
  let calls = 0;
  const result = restoreRelease(f.path, { expectedRoot: f.root, renameSync: (from, to) => {
    if (++calls === 3) throw new Error('failure_after_removal');
    renameSync(from, to);
  } });
  assert.equal(result.ok, false);
  assert.equal(result.safe, true);
  for (const name of ['a', 'b', 'c']) assert.equal(readFileSync(join(f.root, `app/workflow/${name}.py`), 'utf8'), 'new-' + name);
  assert.equal(readFileSync(f.path, 'utf8'), before);
});

test('backup outside journal directory is rejected before replacing any source', async (t) => {
  const { restoreRelease } = await moduleReady;
  const f = multiRelease(t);
  const journalDir = join(f.dir, 'journal-only');
  mkdirSync(journalDir);
  const path = journal(journalDir, f.body);
  const result = restoreRelease(path, { expectedRoot: f.root });
  assert.equal(result.ok, false);
  assert.match(result.error, /backup_missing_or_unsafe.*escapes_root/);
  for (const name of ['a', 'b', 'c']) assert.equal(readFileSync(join(f.root, `app/workflow/${name}.py`), 'utf8'), 'new-' + name);
});

test('restore enforces installer path allowlist before touching any source', async (t) => {
  const { restoreRelease } = await moduleReady;
  const f = multiRelease(t);
  const denied = ['lib/index.js', 'config/model-router.json', '.git/config',
    'app/workflow/operations.py', 'app/workflow/operations_development.py',
    'app/workflow/contracts.py', 'app/workflow/manifest.py',
    'app/workflow/supervisor.py', 'app/workflow/auto.py',
    'app/workflow/runner.py', 'app/workflow/local_agent.py',
    'app/workflow/self_heal.py', 'app/workflow/nested/module.py'];
  for (const path of denied) {
    const target = join(f.root, path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, 'forbidden-installed');
    const body = { ...f.body, changes: [f.body.changes[0],
      { path, base_sha256: 'missing', new_sha256: sha('forbidden-installed') }] };
    journal(f.dir, body);
    const before = readFileSync(f.path, 'utf8');
    const result = restoreRelease(f.path, { expectedRoot: f.root });
    assert.equal(result.ok, false, path);
    assert.match(result.error, /journal_path_not_allowed/, path);
    assert.equal(readFileSync(target, 'utf8'), 'forbidden-installed', path);
    assert.equal(readFileSync(join(f.root, 'app/workflow/a.py'), 'utf8'), 'new-a', path);
    assert.equal(readFileSync(f.path, 'utf8'), before, path);
  }
  const path = 'app/workflow/test_repair.py';
  writeFileSync(join(f.root, path), 'test-installed');
  journal(f.dir, { ...f.body, changes: [f.body.changes[0],
    { path, base_sha256: 'missing', new_sha256: sha('test-installed') }] });
  assert.equal(restoreRelease(f.path, { expectedRoot: f.root }).ok, true);
  assert.equal(existsSync(join(f.root, path)), false, 'allowed test module is restored');
  assert.equal(readFileSync(join(f.root, 'app/workflow/a.py'), 'utf8'), 'old-a');
});

for (const failAt of [2, 4, 7]) {
  test(`injected rename failure ${failAt} restores installed tree and journal`, async (t) => {
    const { restoreRelease } = await moduleReady;
    const f = multiRelease(t);
    const before = readFileSync(f.path, 'utf8');
    let calls = 0;
    const result = restoreRelease(f.path, { expectedRoot: f.root, renameSync: (from, to) => {
      if (++calls === failAt) throw new Error('injected_midrestore_failure');
      renameSync(from, to);
    } });
    assert.equal(result.ok, false);
    assert.equal(result.safe, true);
    assert.match(result.error, /injected_midrestore_failure/);
    for (const name of ['a', 'b', 'c']) assert.equal(readFileSync(join(f.root, `app/workflow/${name}.py`), 'utf8'), 'new-' + name);
    assert.equal(readFileSync(f.path, 'utf8'), before);
    assert.deepEqual(readdirSync(join(f.root, 'app/workflow')).sort(), ['a.py', 'b.py', 'c.py']);
    assert.equal(readdirSync(f.dir).some((name) => name.includes('.restore-')), false);
  });
}
