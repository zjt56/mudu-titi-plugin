"""Fail closed when multi-audio aggregate publication was interrupted."""
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
    multi = any(doc and (doc.get('generation') or
                         str(doc.get('generator', '')).startswith('audio.segment/multi'))
                for doc in documents)
    if not multi:
        return True
    if not mapping or not evidence or not mapping.get('generation') or mapping.get('generation') != evidence.get('generation'):
        return False
    fields = ('unit_id', 'file', 'source_audio', 'source_path', 'source_sha256', 'start', 'end')
    rows, proof = mapping.get('items'), evidence.get('items')
    if not isinstance(rows, list) or not isinstance(proof, list) or not rows or len(rows) != len(proof):
        return False
    try:
        return (len({row['unit_id'] for row in rows}) == len(rows) and
                all({key: row[key] for key in fields} == {key: item[key] for key in fields}
                    for row, item in zip(rows, proof)))
    except (KeyError, TypeError):
        return False
