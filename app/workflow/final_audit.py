"""Read-only consolidation of existing source/content proofs at delivery.

Managed workflows require current source evidence. Standalone payload format
checks remain supported and explicitly say that source audit was not performed.
This does not certify independent original-page coverage or browser rendering.
"""
import hashlib
import json
from pathlib import Path

from . import upload_content
from .findings import source_partition

SOURCE_FILES = ('01_source/正文.txt', '01_source/structure.json',
                '01_source/structure.confirmed.json')


def content_completeness_errors(root):
    """Return deferred content-completeness defects from the types artifact.

    Preassembly may continue with contentComplete=false candidates (user
    2026-11-17: correctness checks move to the final gate). The candidate is
    never rewritten; this read-only check makes the final gate account for it.
    """
    root = Path(root)
    errors = []
    types_path = root / '03_types/types.json'
    if not types_path.is_file():
        return errors
    try:
        types_payload = json.loads(types_path.read_text(encoding='utf-8-sig'))
        type_rows = types_payload.get('items') if isinstance(types_payload, dict) else None
    except (OSError, ValueError) as exc:
        return [{'code': 'FINAL_TYPES_INCOMPLETE', 'message': str(exc)}]
    if not isinstance(type_rows, list):
        return errors
    from .agent_tasks import confirmed_units
    from .question_content import capability_limited_source
    appendix_path = root / '01_source/附录.txt'
    appendix = appendix_path.read_text(encoding='utf-8-sig') if appendix_path.is_file() else ''
    try:
        unit_by_id = {u['unit_id']: u for u in confirmed_units(root)}
    except (OSError, ValueError, KeyError, TypeError):
        unit_by_id = {}
    for row in type_rows:
        if not isinstance(row, dict) or row.get('contentComplete') is not False:
            continue
        uid = row.get('unit_id')
        unit = unit_by_id.get(uid) or {}
        if capability_limited_source(unit, appendix):
            continue
        errors.append({'code': 'CONTENT_COMPLETENESS_UNVERIFIED',
                       'unit_id': uid,
                       'message': '内容完整性未确认（前期候选已保留，最终闸门要求逐题核清）'})
    return errors


def run(root, payload):
    root = Path(root)
    manifest_path = root / 'workflow/manifest.json'
    manifest = {}
    errors = []
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
            if not isinstance(manifest, dict):
                raise ValueError('manifest must be an object')
        except (OSError, ValueError) as exc:
            errors.append({'code': 'FINAL_SOURCE_CONTEXT_INVALID', 'message': str(exc)})
    # Metadata-only manifests were supported by standalone payload tools before
    # workflow stages existed. They are not proof that a source audit occurred.
    managed = ('stages' in manifest or (root / 'workflow/batch-fill-state.json').exists()
               or any((root / rel).exists() for rel in SOURCE_FILES) or bool(errors))
    if not managed:
        return {'status': 'not_applicable', 'passed': None, 'errors': [], 'warnings': [],
                'scope': 'standalone_payload', 'source_sha256': {},
                'unchecked': ['source_fidelity', 'original_page_coverage', 'rendered_preview']}
    hashes = {}
    for relative in SOURCE_FILES:
        try:
            raw = (root / relative).read_bytes()
            hashes[relative] = hashlib.sha256(raw).hexdigest()
            if relative.endswith('.json'):
                if not isinstance(json.loads(raw.decode('utf-8-sig')), dict):
                    raise ValueError('来源结构必须为 JSON object')
            elif not raw.decode('utf-8-sig').strip():
                raise ValueError('原卷正文为空')
        except (OSError, ValueError) as exc:
            errors.append({'code': 'FINAL_SOURCE_CONTEXT_INCOMPLETE', 'path': relative,
                           'message': str(exc)})
    if errors:
        return {'status': 'failed', 'passed': False, 'errors': errors, 'warnings': [],
                'scope': 'confirmed_units_and_bound_content', 'source_sha256': hashes,
                'unchecked': ['source_fidelity', 'original_page_coverage', 'rendered_preview']}
    deferred_errors = content_completeness_errors(root)
    from .runner import source_audit_for_batch
    audit = source_audit_for_batch(root, payload)
    audit_errors, limited = source_partition(audit.get('issues') or [])
    errors = errors + deferred_errors + list(audit_errors)
    if audit.get('passed') is not True and not audit.get('issues'):
        errors.append({'code': 'FINAL_SOURCE_AUDIT_INCOMPLETE', 'message': '原卷逐题核对未完成且没有可判定证据'})
    content = upload_content.inspect_content(root, payload, strict=True)
    errors = errors + list(content.get('errors') or [])
    if content.get('passed') is not True and not content.get('errors'):
        errors.append({'code': 'FINAL_CONTENT_AUDIT_INCOMPLETE', 'message': '原答案/解析严格核对未完成'})
    hashes.update(content.get('source_sha256') or {})
    return {'status': 'failed' if errors else 'passed', 'passed': not errors,
            'scope': 'confirmed_units_and_bound_content', 'errors': errors,
            'warnings': limited + list(content.get('warnings') or []),
            'source_sha256': hashes,
            'unchecked': ['independent_original_page_coverage', 'rendered_preview']}
