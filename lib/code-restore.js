import { readFileSync, writeFileSync, unlinkSync, mkdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, resolve, relative, isAbsolute, sep } from 'node:path';

// Restore an installed code release from its journal after a boot that never
// became healthy. This is the Host half of the transaction the backend began:
// the backend can only roll back while it is still running, and a release that
// breaks the boot cannot use code that will not start. Only files the plan
// actually replaced are touched; a file changed again since install (by a
// developer or another release) is a hard error, never silently overwritten.
function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function inside(root, target) {
  const rel = relative(resolve(root), resolve(target));
  return rel && !rel.startsWith('..') && !isAbsolute(rel) && !rel.split(sep).includes('..');
}

export function restoreRelease(journalPath) {
  let journal;
  try {
    journal = JSON.parse(readFileSync(journalPath, 'utf8'));
  } catch (error) {
    return { ok: false, error: 'journal_unreadable: ' + String(error?.message || error) };
  }
  if (journal.status !== 'installed') {
    return { ok: false, error: 'journal_not_installed: ' + String(journal.status || '') };
  }
  const root = String(journal.source_root || '');
  const changes = Array.isArray(journal.changes) ? journal.changes : [];
  if (!root || !changes.length) return { ok: false, error: 'journal_incomplete' };
  // Check every file before writing any of them: a partial restore is worse
  // than none, because the tree would be a mix of two releases.
  for (const change of changes) {
    const target = resolve(root, change.path);
    if (!inside(root, target)) return { ok: false, error: 'journal_path_escapes_root: ' + change.path };
    if (change.new_sha256 && existsSync(target) && sha256(readFileSync(target)) !== change.new_sha256) {
      return { ok: false, error: 'source_changed_after_install: ' + change.path };
    }
  }
  const restored = [];
  for (const change of changes) {
    const target = resolve(root, change.path);
    if (change.base_sha256 === 'missing') {
      if (existsSync(target) && (!change.new_sha256 || sha256(readFileSync(target)) === change.new_sha256)) {
        unlinkSync(target);
        restored.push(change.path);
      }
      continue;
    }
    const stored = (journal.backups || {})[change.path];
    if (!stored || !existsSync(stored)) return { ok: false, error: 'backup_missing: ' + change.path, restored };
    const bytes = readFileSync(stored);
    if (change.base_sha256 && sha256(bytes) !== change.base_sha256) {
      return { ok: false, error: 'backup_corrupt: ' + change.path, restored };
    }
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, bytes);
    restored.push(change.path);
  }
  try {
    journal.status = 'rolled_back';
    journal.restored = restored;
    writeFileSync(journalPath, JSON.stringify(journal, null, 1));
  } catch {}
  return { ok: true, restored, root };
}
