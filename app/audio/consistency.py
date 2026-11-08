"""Fail closed when audio evidence is stale, incomplete, or tampered."""
import json
from pathlib import Path


def read_pair(batch):
    directory = Path(batch) / '02_audio'
    documents = []
    for name in ('audio_map.json', 'segment-evidence.json'):
        try:
            value = json.loads((directory / name).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            value = None
        documents.append(value if isinstance(value, dict) else None)
    return documents


def pair_is_consistent(batch, documents=None):
    documents = read_pair(batch) if documents is None else documents
    mapping, evidence = documents
    if (Path(batch) / '02_audio/SKIP_LISTENING').is_file():
        return True
    if not evidence:
        return not mapping or not mapping.get('items')
    rows = evidence.get('items')
    if not isinstance(rows, list):
        return False
    # A cut with no semantic source binding must never be treated as current.
    binding = evidence.get('unit_binding')
    if not isinstance(binding, dict):
        return not rows and not evidence.get('generator')
    try:
        from .binding import assert_current
        assert_current(batch, binding)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    _, listening = binding.get('version'), binding.get('listening_units')
    expected = {row['unit_id'] for row in listening} if isinstance(listening, list) else set()
    if not expected or not isinstance(listening, list):
        return False
    if len(rows) != len(expected) or {row.get('unit_id') for row in rows if isinstance(row, dict)} != expected:
        return False
    root = Path(batch).resolve()
    try:
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('file'), str):
                return False
            path = (root / row['file']).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return False
            if not row.get('file_sha256') or _digest(path) != row['file_sha256']:
                return False
        if mapping is not None:
            if mapping.get('unit_binding') not in (None, binding):
                return False
            if mapping.get('generation') and mapping.get('unit_binding') != binding:
                return False
            map_rows = mapping.get('items')
            if not isinstance(map_rows, list) or len(map_rows) != len(rows):
                return False
            by_id = {row['unit_id']: row for row in rows}
            for item in map_rows:
                source = by_id.get(item.get('unit_id')) if isinstance(item, dict) else None
                if source is None or any(item.get(key) != source.get(key)
                                         for key in ('file', 'start', 'end')):
                    return False
    except (OSError, TypeError, ValueError):
        return False
    return True


def _digest(path):
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
