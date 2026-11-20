"""Read-only consolidation of existing source/content proofs at delivery.

Managed workflows require current source evidence. Standalone payload format
checks remain supported and explicitly say that source audit was not performed.
This does not certify independent original-page coverage or browser rendering.
"""
import hashlib
import json
import re
from pathlib import Path

from . import upload_content
from .findings import source_partition

SOURCE_FILES = ('01_source/正文.txt', '01_source/structure.json',
                '01_source/structure.confirmed.json')
STRUCTURE_REPORT = '07_review/structure-gate.json'


def _valid_sequential_writing_warning(root, issue):
    """Accept a source-bound unnumbered writing section's internal next number.

    The printed section title may use a Roman numeral and the writing prompt may
    have no Arabic number.  A confirmed ``single_writing_section`` is allowed to
    use the next number after the preceding numbered unit for local continuity;
    this is not evidence that the number was printed in the source.
    """
    if str(issue.get('code') or '') != 'QUESTION_NUMBER_INFERRED':
        return False
    try:
        confirmed = json.loads((Path(root) / '01_source/structure.confirmed.json').read_text(encoding='utf-8-sig'))
        units = confirmed.get('units') if isinstance(confirmed, dict) else []
        writing = [u for u in units if isinstance(u, dict)
                   and u.get('unit_kind') == 'standalone'
                   and isinstance(u.get('source_span'), dict)
                   and u['source_span'].get('number_binding') == 'single_writing_section'
                   and '书面表达' in str(u.get('type') or '')]
        if len(writing) != 1:
            return False
        numbers = writing[0].get('numbers') or []
        if len(numbers) != 1 or type(numbers[0]) is not int or numbers[0] <= 0:
            return False
        prior = [n for u in units if u is not writing[0]
                 for n in (u.get('numbers') or []) if type(n) is int and n > 0]
        return bool(prior) and numbers[0] == max(prior) + 1
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _scoped_numbering_restart_warning(root, issue):
    """Accept numbering that restarts inside each printed 大题 of the lesson.

    The structure report defers every structure doubt to this final gate.  A
    restart is resolved only when the confirmed structure proves the original
    paper partition instead of a global number line: every unit is bound to its
    own section occurrence (``identity_version='scoped-v1'`` plus
    ``section_id``/``source_section``), its span stays inside that section, and
    at least two distinct 大题 occurrences print the same numbers.  Without that
    proof the deferred evidence stays blocking; numbers are never merged into
    one whole-paper sequence.
    """
    if str(issue.get('code') or '') != 'NUMBERING_RESTART':
        return False
    try:
        confirmed = json.loads((Path(root) / '01_source/structure.confirmed.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return False
    units = confirmed.get('units') if isinstance(confirmed, dict) else None
    if not isinstance(units, list) or not units:
        return False
    occurrences, seen_units = {}, set()
    for unit in units:
        if not isinstance(unit, dict) or unit.get('identity_version') != 'scoped-v1':
            return False
        unit_id = unit.get('unit_id')
        if not isinstance(unit_id, str) or not unit_id.strip() or unit_id in seen_units:
            return False
        seen_units.add(unit_id)
        if not str(unit.get('section_id') or '').strip():
            return False
        span, section = unit.get('source_span'), unit.get('source_section')
        if not isinstance(span, dict) or not isinstance(section, dict):
            return False
        start, end = span.get('start_line'), span.get('end_line')
        sec_start, sec_end = section.get('start_line'), section.get('end_line')
        occurrence = section.get('occurrence')
        numbers = unit.get('numbers')
        if type(start) is not int or type(end) is not int or not 0 < start <= end:
            return False
        if type(sec_start) is not int or type(sec_end) is not int or not sec_start < start <= end <= sec_end:
            return False
        if type(occurrence) is not int or occurrence < 1:
            return False
        if not isinstance(numbers, list) or not numbers:
            return False
        if any(type(number) is not int or number < 1 for number in numbers):
            return False
        if len(set(numbers)) != len(numbers):
            return False
        occurrences.setdefault(occurrence, set()).update(numbers)
    if len(occurrences) < 2:
        return False
    groups = list(occurrences.values())
    return any(first & second for index, first in enumerate(groups) for second in groups[index + 1:])


def structure_evidence(root, manifest, hashes):
    """Retain deferred structure defects only from a currently bound report."""
    path = root / STRUCTURE_REPORT
    structure_record = (manifest.get('stages') or {}).get('structure_gate') or {}
    # Older format-repair batches may predate the structure report. Preserve
    # compatibility for those isolated artifacts while marking the evidence
    # boundary explicitly. Current managed workflows must provide the report.
    requires_report = bool(structure_record) or (root / 'workflow/batch-fill-state.json').exists()
    if not path.is_file() and not requires_report:
        return ([], [{'code': 'FINAL_STRUCTURE_EVIDENCE_UNCHECKED',
                      'path': STRUCTURE_REPORT,
                      'message': '旧格式修复批次未生成结构报告，结构延期检查未执行'}])
    try:
        raw = path.read_bytes()
        report = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(report, dict) or report.get('passed') is not True:
            raise ValueError('结构确认报告缺失或未通过')
        binding = report.get('input_hashes')
        if not isinstance(binding, dict) or any(not binding.get(rel) or binding[rel] != hashes.get(rel)
                                                 for rel in SOURCE_FILES):
            raise ValueError('结构确认报告与当前正文或结构不一致')
        original = manifest.get('inputs', {}).get('source', {}).get('path')
        if not original or not report.get('original_sha256'):
            raise ValueError('结构确认缺少原件绑定')
        if hashlib.sha256(Path(original).read_bytes()).hexdigest() != report['original_sha256']:
            raise ValueError('结构确认原件摘要已变化')
        issues = report.get('structure_warnings', [])
        if not isinstance(issues, list) or any(not isinstance(item, dict) for item in issues):
            raise ValueError('结构警告记录损坏')
        hashes[STRUCTURE_REPORT] = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return ([{'code': 'FINAL_STRUCTURE_EVIDENCE_INCOMPLETE', 'path': STRUCTURE_REPORT,
                  'message': str(exc)}], [])
    errors, warnings = [], []
    for item in issues:
        issue = dict(item)
        code = str(issue.get('code') or '')
        limited = (code in {'OCR_VISUAL_REVIEW', 'SOURCE_IMAGE_MISSING',
                            'SOURCE_METADATA_CONFLICT'} or bool(re.match(r'^W\d', code))
                   or (issue.get('failure_class') == 'capability_limited'
                       and issue.get('comparison_performed') is False))
        # A source-proven writing section may have a local sequential number even
        # when the printed source has no Arabic question number.  Keep the
        # evidence as a warning, but do not block the upload-format gate.
        sequential_writing = _valid_sequential_writing_warning(root, issue)
        scoped_restart = _scoped_numbering_restart_warning(root, issue)
        issue['blocking'] = not limited and not sequential_writing and not scoped_restart
        if sequential_writing:
            issue['message'] = str(issue.get('message') or '') + '；已按上一题号顺承为本地编号，非印刷题号'
        elif scoped_restart:
            issue['message'] = str(issue.get('message') or '') + '；已按原卷大题分区定位，逐大题内部题号独立编号，非全卷题号合并'
        issue['stage'] = 'format_gate'
        issue['report'] = STRUCTURE_REPORT
        issue.pop('status', None)
        (warnings if limited or sequential_writing or scoped_restart else errors).append(issue)
    return errors, warnings


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
        if not isinstance(row, dict):
            errors.append({'code': 'FINAL_TYPES_INCOMPLETE', 'message': '题型记录必须是对象'})
            continue
        if 'contentComplete' in row and not isinstance(row['contentComplete'], bool):
            errors.append({'code': 'FINAL_TYPES_INCOMPLETE', 'unit_id': row.get('unit_id'),
                           'message': 'contentComplete 必须是布尔值'})
            continue
        if row.get('contentComplete') is not False:
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
    structure_errors, structure_warnings = structure_evidence(root, manifest, hashes)
    deferred_errors = content_completeness_errors(root)
    from .runner import source_audit_for_batch
    audit = source_audit_for_batch(root, payload)
    audit_errors, limited = source_partition(audit.get('issues') or [])
    errors = errors + structure_errors + deferred_errors + list(audit_errors)
    if audit.get('passed') is not True and not audit.get('issues'):
        errors.append({'code': 'FINAL_SOURCE_AUDIT_INCOMPLETE', 'message': '原卷逐题核对未完成且没有可判定证据'})
    content = upload_content.inspect_content(root, payload, strict=True)
    errors = errors + list(content.get('errors') or [])
    if content.get('passed') is not True and not content.get('errors'):
        errors.append({'code': 'FINAL_CONTENT_AUDIT_INCOMPLETE', 'message': '原答案/解析严格核对未完成'})
    hashes.update(content.get('source_sha256') or {})
    return {'status': 'failed' if errors else 'passed', 'passed': not errors,
            'scope': 'confirmed_units_and_bound_content', 'errors': errors,
            'warnings': structure_warnings + limited + list(content.get('warnings') or []),
            'source_sha256': hashes,
            'unchecked': ['independent_original_page_coverage', 'rendered_preview']}
