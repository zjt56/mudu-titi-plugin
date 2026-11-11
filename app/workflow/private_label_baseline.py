"""Source-bound, uncommitted label units for first-delivery scoped recovery.

Receipts are not canonical artifacts or proof of acceptance. Every consumer
revalidates their payload against current code and source before merging in RAM.

Bindings are computed from the batch's source files themselves, never from a
stage task's input list: a full first-delivery prompt and a narrowed repair
prompt hash different subsets (an answer source appears only once feedback is
supplied, the unconfirmed structure only in the wide prompt). Keying receipts on
those lists would lose an already-verified unit whenever the prompt shape
changed, which is exactly the first-delivery case this module serves.
"""
import copy
import hashlib
import json
import os
from pathlib import Path

STAGES = frozenset(('answers', 'explanations'))
CONTEXT_KEY = 'private_label_baseline'
SOURCE_DIRS = ('01_source', '01_source_ans')
ANCHOR = '06_batch/question-draft.json'


def source_binding(batch):
    """Hash the batch-owned source truth a unit's validation can consult."""
    batch = Path(batch)
    binding = {}
    for name in SOURCE_DIRS:
        base = batch / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            binding[path.relative_to(batch).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    anchor = batch / ANCHOR
    binding[ANCHOR] = (hashlib.sha256(anchor.read_bytes()).hexdigest()
                       if anchor.is_file() else 'MISSING')
    return binding


def _external(hashes):
    """Absolute batch-owned answer sources recorded outside the batch tree.

    Repository rules/catalog files are not evidence: a code fix may change them,
    and the saved candidate is revalidated before reuse anyway. Any other
    absolute file *is* evidence and stays bound to the receipt.
    """
    from . import agent_tasks
    binding = {}
    for key, value in hashes.items():
        if not Path(key).is_absolute():
            continue
        try:
            Path(key).resolve().relative_to(agent_tasks.ROOT.resolve())
            continue
        except ValueError:
            pass
        binding[key] = value
    return binding


def _path(batch, stage, uid, binding):
    encoded = json.dumps({'version': 1, 'stage': stage, 'unit_id': uid, 'inputs': binding},
                         ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    key = hashlib.sha256(encoded).hexdigest()
    return Path(batch) / 'workflow' / 'agent-runs' / (stage + '-baseline') / (key + '.json')


def store(batch, stage, uid, response, extra=None):
    """Called only after current per-unit validation, never for failed candidates."""
    if stage not in STAGES:
        return
    binding = source_binding(batch)
    path = _path(batch, stage, uid, binding)
    record = {'version': 1, 'stage': stage, 'unit_id': uid,
              'input_hashes': {**binding, **_external(extra or {})},
              'response': copy.deepcopy(response)}
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding='utf-8') == encoded:
        return
    temporary = path.with_suffix('.tmp')
    temporary.write_text(encoded, encoding='utf-8')
    os.replace(temporary, path)


def _current(batch, recorded):
    """Recompute every hash a receipt claims; anything missing or different fails.

    Newly arrived batch source files are not part of an older receipt: the
    per-unit revalidation below, not the key, decides whether the candidate is
    still acceptable.
    """
    binding = source_binding(batch)
    current = {key: binding[key] for key in recorded if key in binding}
    for key in recorded:
        if key in current:
            continue
        path = Path(key)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            return None
        current[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return current


def load(batch, stage, units, context, *, expected=None):
    """Read exact current-source receipt paths; reject missing/stale/invalid units.

    Return a complete validated untouched payload plus precise receipt hashes.
    No directory scan, fallback to another binding, canonical write, or model call.
    """
    from . import agent_tasks, label_execution, scoped_regeneration, unit_tasks
    if stage not in STAGES:
        raise ValueError('Private scoped baseline unsupported for ' + stage)
    responses, parts, receipts, problems = [], [], {}, []
    binding = source_binding(batch)
    for unit in units:
        uid = unit['unit_id']
        path = _path(batch, stage, uid, binding)
        relative = path.relative_to(Path(batch)).as_posix()
        try:
            safe = agent_tasks._safe_path(batch, relative)
            raw = safe.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if expected is not None and expected.get(relative) != digest:
                raise ValueError('private checkpoint changed during scoped repair')
            record = json.loads(raw.decode('utf-8'))
            recorded = record.get('input_hashes')
            if (record.get('version') != 1 or record.get('stage') != stage
                    or record.get('unit_id') != uid or not isinstance(recorded, dict)
                    or recorded != _current(batch, recorded)):
                raise ValueError('private checkpoint binding mismatch')
            response = record['response']
            payload = label_execution._envelope(stage, response, [unit])
            if response['status'] != 'completed' or any(
                    f['severity'] in ('major', 'blocker') for f in response['findings']):
                raise ValueError('private checkpoint is not a completed unit')
            candidate = copy.deepcopy(payload)
            scoped = scoped_regeneration.project(stage, context, [unit])
            agent_tasks._validate(Path(batch), stage, candidate, scoped, [unit])
            if candidate != payload:
                raise ValueError('untouched private checkpoint requires content changes')
            receipts[relative] = digest
            responses.append(copy.deepcopy(response))
            parts.append({'unit_ids': [uid]})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(uid + ': ' + str(exc))
    if problems:
        raise ValueError('Scoped repair cannot validate private baseline units: ' + '; '.join(problems))
    if expected is not None and receipts != expected:
        raise ValueError('Private baseline receipt coverage changed during scoped repair')
    merged = unit_tasks.merge(stage, parts, responses)
    return merged['artifacts'][agent_tasks.ARTIFACTS[stage]], receipts
