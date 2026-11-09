const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } = require('node:fs');
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
  const result = restoreRelease(path);
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
  const result = restoreRelease(path);
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
  const result = restoreRelease(path);
  assert.equal(result.ok, false);
  assert.match(result.error, /escapes_root/);
  rmSync(dir, { recursive: true, force: true });
});
