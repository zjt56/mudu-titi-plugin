import { readFileSync, writeFileSync, unlinkSync, lstatSync, realpathSync, renameSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { dirname, resolve, relative, isAbsolute, sep, join } from 'node:path';

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const digest = /^[a-f0-9]{64}$/;
// Match the isolated installer boundary in operations_development.allowed().
// A forged journal must not restore host code, configuration or control files.
const restorablePath = /^app\/workflow\/[a-z][a-z0-9_]*\.py$/;
const lockedPaths = new Set([
  'app/workflow/operations.py', 'app/workflow/operations_development.py',
  'app/workflow/contracts.py', 'app/workflow/manifest.py',
  'app/workflow/supervisor.py', 'app/workflow/auto.py',
  'app/workflow/runner.py', 'app/workflow/local_agent.py',
  'app/workflow/self_heal.py',
]);
function inside(root, target) {
  const rel = relative(root, target);
  return rel && rel !== '..' && !rel.startsWith('..' + sep) && !isAbsolute(rel);
}

// Check each component without following links, including dangling links. The
// host supplies the trusted root; journal contents cannot choose another tree.
function regular(root, target) {
  if (!inside(root, target)) throw new Error('journal_path_escapes_root: ' + target);
  let cursor = root;
  const parts = relative(root, target).split(sep);
  for (let i = 0; i < parts.length; i++) {
    cursor = join(cursor, parts[i]);
    const stat = lstatSync(cursor);
    if (stat.isSymbolicLink()) throw new Error('symlink_component: ' + cursor);
    if (i < parts.length - 1 ? !stat.isDirectory() : !stat.isFile()) {
      throw new Error('not_regular_path: ' + cursor);
    }
  }
  return lstatSync(target);
}

// Stage the entire restore before replacing anything. Keep the installed files
// by rename until both the source replacements and journal update have committed.
// An ordinary write/rename failure restores the complete installed tree instead
// of leaving a mixture of releases. rename is injectable for fault tests only.
export function restoreRelease(journalPath, options = {}) {
  const staged = [];
  const moved = [];
  let journal;
  let root;
  let journalTemporary;
  const replace = options.renameSync || renameSync;
  const cleanup = (path) => { try { unlinkSync(path); } catch {} };
  try {
    if (!options.expectedRoot || !isAbsolute(options.expectedRoot)) throw new Error('expected_root_required');
    root = resolve(options.expectedRoot);
    if (lstatSync(root).isSymbolicLink() || !lstatSync(root).isDirectory()) throw new Error('source_root_invalid');
    root = realpathSync(root);
    const journalRoot = dirname(resolve(journalPath));
    if (lstatSync(journalRoot).isSymbolicLink() || !lstatSync(journalRoot).isDirectory()) throw new Error('journal_directory_unsafe');
    journalPath = resolve(journalPath);
    regular(journalRoot, journalPath);
    journal = JSON.parse(readFileSync(journalPath, 'utf8'));
    if (journal.status !== 'installed') throw new Error('journal_not_installed: ' + String(journal.status || ''));
    if (typeof journal.source_root !== 'string' || resolve(journal.source_root) !== resolve(options.expectedRoot)) {
      throw new Error('journal_root_mismatch');
    }
    const changes = journal.changes;
    if (!Array.isArray(changes) || !changes.length || changes.length > 256) throw new Error('journal_incomplete');
    const seen = new Set();
    let total = 0;
    const entries = changes.map((change) => {
      const path = change?.path;
      if (typeof path !== 'string' || !path || isAbsolute(path) || path.includes('\\') || path.split('/').some((p) => !p || p === '.' || p === '..')) {
        throw new Error('journal_path_escapes_root: ' + path);
      }
      if (!restorablePath.test(path) || lockedPaths.has(path)) throw new Error('journal_path_not_allowed: ' + path);
      const target = resolve(root, path);
      if (seen.has(target) || target === journalPath) throw new Error('journal_duplicate_path: ' + path);
      seen.add(target);
      if (!digest.test(change.new_sha256) || (change.base_sha256 !== 'missing' && !digest.test(change.base_sha256))) {
        throw new Error('journal_hash_invalid: ' + path);
      }
      const stat = regular(root, target);
      if (stat.size > 8_000_000) throw new Error('source_too_large: ' + path);
      if (sha256(readFileSync(target)) !== change.new_sha256) throw new Error('source_changed_after_install: ' + path);
      let bytes = null;
      if (change.base_sha256 !== 'missing') {
        const stored = journal.backups?.[path];
        if (typeof stored !== 'string' || !isAbsolute(stored)) throw new Error('backup_missing: ' + path);
        const backup = resolve(stored);
        let backupStat;
        try { backupStat = regular(journalRoot, backup); }
        catch (error) { throw new Error('backup_missing_or_unsafe: ' + path + ': ' + error.message); }
        if (backupStat.size > 8_000_000) throw new Error('backup_too_large: ' + path);
        bytes = readFileSync(backup);
        if (sha256(bytes) !== change.base_sha256) throw new Error('backup_corrupt: ' + path);
      }
      total += stat.size + (bytes?.length || 0);
      if (total > 64_000_000) throw new Error('restore_too_large');
      return { path, target, bytes, mode: stat.mode, installedHash: change.new_sha256 };
    });
    // All backups, hashes and path components passed before any temp is written.
    for (const entry of entries) {
      const suffix = '.restore-' + randomUUID();
      entry.saved = entry.target + suffix + '.installed';
      entry.temporary = entry.bytes === null ? null : entry.target + suffix + '.staged';
      staged.push(entry);
      if (entry.temporary) writeFileSync(entry.temporary, entry.bytes, { flag: 'wx', mode: entry.mode & 0o777 });
    }
    journalTemporary = journalPath + '.restore-' + randomUUID();
    writeFileSync(journalTemporary, JSON.stringify({ ...journal, status: 'rolled_back', restored: entries.map((e) => e.path) }, null, 1), { flag: 'wx', mode: 0o600 });
    for (const entry of entries) {
      regular(root, entry.target);
      if (sha256(readFileSync(entry.target)) !== entry.installedHash) throw new Error('source_changed_after_install: ' + entry.path);
      replace(entry.target, entry.saved);
      moved.push(entry);
      if (entry.temporary) replace(entry.temporary, entry.target);
    }
    replace(journalTemporary, journalPath);
    journalTemporary = null;
    for (const entry of moved) cleanup(entry.saved);
    return { ok: true, restored: entries.map((e) => e.path), root };
  } catch (error) {
    const rollbackErrors = [];
    for (const entry of moved.reverse()) {
      try {
        // Native rename also replaces a partially restored target atomically.
        regular(root, entry.saved);
        renameSync(entry.saved, entry.target);
      } catch (failure) { rollbackErrors.push(entry.path + ': ' + String(failure.message || failure)); }
    }
    return { ok: false, error: String(error?.message || error), restored: [],
      ...(rollbackErrors.length ? { rollbackErrors, safe: false } : { safe: true }) };
  } finally {
    for (const entry of staged) if (entry.temporary) cleanup(entry.temporary);
    if (journalTemporary) cleanup(journalTemporary);
  }
}
