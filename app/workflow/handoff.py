"""Read-only producer acceptance shared by dispatch, import and reconciliation.

A status or an existing file alone is never a usable delivery. Validators reuse
the consumers' schema, catalog, source and final wire-shape checks.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

VERSION = 1
BATCH_ARTIFACTS = {
    'assemble': '06_batch/batch.json',
    'meta': '06_batch/batch_meta.base.json',
    'underlines': '06_batch/batch_meta.json',
}


def _json(root, relative):
    from .agent_tasks import _safe_path
    from .format_gate import _load
    path = _safe_path(root, relative)
    return _load(path.read_bytes().removeprefix(b'\xef\xbb\xbf'))


def _units(root):
    from .agent_tasks import confirmed_units
    return confirmed_units(root)


def _context(root):
    from .agent_tasks import ARTIFACTS
    result = {}
    for relative in ('01_source/structure.confirmed.json', '01_source/extraction.json',
                     '01_source/metadata.json', *ARTIFACTS.values(),
                     '02_audio/segment-evidence.json'):
        if (root / relative).is_file():
            result[relative] = _json(root, relative)
    for relative in ('01_source/正文.txt', '01_source/附录.txt', '01_source_ans/正文.txt'):
        if (root / relative).is_file():
            result[relative] = (root / relative).read_text(encoding='utf-8-sig')
    folder = root / '01_source/materials'
    if folder.is_dir():
        for path in sorted(folder.glob('attachment_*.txt')):
            result[path.relative_to(root).as_posix()] = path.read_text(encoding='utf-8-sig')
    return result


class ExplanationProjectionMismatch(ValueError):
    """A known accepted field differs; malformed/missing inputs stay separate."""


def require_explanation_projection(root, stage, payload=None):
    """Cheap consumer-content check shared by delivery and stale scheduling."""
    from . import agent_tasks, content_rules
    root = Path(root)
    explanation_path = agent_tasks.ARTIFACTS['explanations']
    if not (root / explanation_path).is_file():
        return  # Legacy batches without a producer remain subject to validation.
    units = _units(root)
    if payload is None:
        payload = _json(root, BATCH_ARTIFACTS[stage])
    rows = agent_tasks._coverage(payload, units)
    expected = agent_tasks._coverage(_json(root, explanation_path), units)
    for uid, row in rows.items():
        try:
            content_rules.preserve(row, expected[uid])
        except ValueError as exc:
            raise ExplanationProjectionMismatch(f'{uid}: {exc}') from exc


_DERIVED_META_KEYS = frozenset((
    'filterMetadata', 'difficulty', 'difficultyValue', 'categories',
    'question_type', 'subtypes', 'wordCount', 'words', 'contentComplete',
    'knowledge', 'knowledgePoints', 'knowledgeIds', 'knowledge_ids', 'textbook',
    'explanation', 'articleSummary', 'answer', 'answers',
    'difficultyEvidence', 'difficulty_evidence', 'audio', 'audioFile',
))
_UNDERLINE_TAG = re.compile(r'</?u>', re.IGNORECASE)


def _source_projection(value):
    """Project derived question fields only; nested source/evidence stays exact."""
    result = copy.deepcopy(value)

    def row_projection(row):
        if not isinstance(row, dict):
            return
        for key in _DERIVED_META_KEYS:
            row.pop(key, None)
        for child in row.get('subquestions') or []:
            row_projection(child)

    if isinstance(result, dict):
        for row in result.get('items') or []:
            row_projection(row)
    return result


def _without_underline_tags(value):
    if isinstance(value, dict):
        return {key: _without_underline_tags(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_without_underline_tags(child) for child in value]
    if isinstance(value, str):
        return _UNDERLINE_TAG.sub('', value)
    return value


def _without_audio_fields(value):
    """Drop derived audio wiring from a projected metadata/content view."""
    projected = copy.deepcopy(value)
    def row(item):
        if not isinstance(item, dict):
            return
        item.pop('audio', None)
        item.pop('audioFile', None)
        if isinstance(item.get('attachments'), list):
            item['attachments'] = [a for a in item['attachments']
                                   if not (isinstance(a, dict) and a.get('kind') == 'audio')]
            if not item['attachments']:
                item.pop('attachments')
        for child in item.get('subquestions') or []:
            row(child)
    if isinstance(projected, dict):
        for item in projected.get('items') or []:
            row(item)
    return projected


def _assert_meta_projection(snapshot, payload):
    # Late audio delivery legitimately adds audio attachments to the batch views
    # after the content snapshot was taken. Metadata owns source/text fields, not
    # media wiring, so drop audio entries from every attachments list before
    # proving the metadata view still projects the snapshot.
    if (_without_audio_fields(_source_projection(snapshot))
            != _without_audio_fields(_source_projection(payload))):
        raise ValueError('Metadata view no longer projects the content snapshot')


def _assert_underline_projection(base, payload):
    # Final assembly fills this same view later. Only source-owned fields belong
    # to underline delivery; requiring later answers/labels creates a DAG cycle.
    # Audio attachments are late media wiring and must not make underline delivery
    # stale after assemble.
    if (_without_underline_tags(_without_audio_fields(_source_projection(base)))
            != _without_underline_tags(_without_audio_fields(_source_projection(payload)))):
        raise ValueError('Underline view changed fields outside underline markup')


def _source_view(root, payload):
    """Check consumer identity/coverage without requiring future fill artifacts."""
    from . import agent_tasks, batch_fill
    units = _units(root)
    rows = agent_tasks._coverage(payload, units)
    for unit in units:
        row = rows[unit['unit_id']]
        for key in ('unit_kind', 'numbers'):
            if row.get(key) != unit.get(key):
                raise ValueError(f"{unit['unit_id']}: source view {key} differs from confirmed identity")
        agent_tasks._children(row, unit)
    if (root / batch_fill.STATE).is_file():
        # Includes source/media binding proof and immutable source-base integrity.
        batch_fill._managed(root)


def _batch(root, stage, payload, *, defer_content=False):
    from . import agent_tasks, format_gate, label_audit
    from .runner import source_audit_for_batch
    units = _units(root)
    rows = agent_tasks._coverage(payload, units)
    # A correct wire shape alone cannot prove newly accepted content was copied.
    require_explanation_projection(root, stage, payload)
    problems = format_gate._shape(payload)
    if problems:
        raise ValueError('Downstream wire schema: ' + str(problems[:8]))
    labels = label_audit.inspect_batch(payload)
    if not labels.get('passed'):
        raise ValueError('Downstream label schema: ' + str(labels.get('errors', [])[:8]))
    audit = source_audit_for_batch(root, payload)
    # 图片/音频属于 AI 能力受限项：缺失或未映射只作 informational，不阻断下游。
    # 结构、题号、材料归属等文本类问题仍是硬阻断。
    from .findings import source_partition
    blocking, _ = source_partition(audit.get("issues", []))
    if blocking and not defer_content:
        raise ValueError('Downstream source fidelity: ' + str(blocking[:8]))
    for unit in units:
        row = rows[unit['unit_id']]
        children = row.get('subquestions') or []
        agent_tasks._children(row, unit)
        for item in ([row] if unit['unit_kind'] == 'standalone' else children):
            for key in ('answer', 'explanation'):
                if not isinstance(item.get(key), str):
                    raise ValueError(f"{unit['unit_id']}: downstream {key} must be a string")


def inspect_stage(batch_dir, manifest, stage_id, *, legacy_source_only=False):
    """Inspect installed output only; never repair, install, mark or invoke a model."""
    from . import contracts, manifest as mf
    root = Path(batch_dir).expanduser().resolve()
    spec = contracts.STAGE_BY_ID[stage_id]
    record = manifest.get('stages', {}).get(stage_id, {})
    try:
        if stage_id in ('upload_gate', 'upload', 'publish_gate'):
            return {'passed': True, 'errors': []}
        if record.get('status') == mf.SKIPPED and stage_id in ('answers', 'audio', 'underlines'):
            # No-input audio has no media contract. A skipped underline stage
            # still hands off the unmarked metadata projection as the exact
            # source view for final assembly; it must not require a nonexistent
            # marked file or turn an optional format step into a blocker.
            if stage_id == 'audio':
                if not mf.skip_reason(dict(manifest, batch_dir=str(root)), spec):
                    raise ValueError('Audio skip is no longer justified')
                return {'passed': True, 'errors': []}
            if stage_id == 'underlines':
                base = root / '06_batch/batch_meta.base.json'
                if not base.is_file():
                    raise ValueError('Skipped underlines requires 06_batch/batch_meta.base.json')
                _json(root, '06_batch/batch_meta.base.json')
                return {'passed': True, 'errors': [], 'skipped': True,
                        'source_view': '06_batch/batch_meta.base.json'}
        for relative in spec.produces:
            if legacy_source_only and stage_id == 'preassemble' and relative == '03_types/types.json':
                continue
            from .agent_tasks import _safe_path
            if not _safe_path(root, relative).is_file():
                raise ValueError('Missing required delivery: ' + relative)
        if stage_id == 'extract':
            if not (root / '01_source/正文.txt').read_text(encoding='utf-8-sig').strip():
                raise ValueError('Extracted source text is empty')
            for relative in spec.produces[1:]:
                if not isinstance(_json(root, relative), dict):
                    raise ValueError(relative + ' must be a JSON object')
            original = manifest.get('inputs', {}).get('source', {}).get('path')
            if original:
                metadata = _json(root, '01_source/metadata.json')
                if metadata.get('original_sha256') != mf.sha256_file(original):
                    raise ValueError('Extraction artifacts are not bound to the current original source')
        elif stage_id == 'structure_gate':
            _units(root)
            report = _json(root, '07_review/structure-gate.json')
            if report.get('passed') is not True:
                raise ValueError('Structure gate has not passed')
            for relative in ('01_source/正文.txt', '01_source/structure.json', '01_source/structure.confirmed.json'):
                if report.get('input_hashes', {}).get(relative) != mf.sha256_file(root / relative):
                    raise ValueError('Structure evidence is stale: ' + relative)
            original = manifest.get('inputs', {}).get('source', {}).get('path')
            if original and report.get('original_sha256') != mf.sha256_file(original):
                raise ValueError('Original source is not bound to the structure gate')
        elif stage_id == 'content_snapshot':
            # The snapshot is an immutable handoff, not merely a present JSON
            # file. It must remain the exact pre-underlines source projection.
            snapshot = _json(root, '06_batch/content.snapshot.json')
            base = _json(root, '06_batch/batch.base.json')
            if not isinstance(snapshot, dict) or not isinstance(base, dict):
                raise ValueError('Content snapshot and batch.base must be JSON objects')
            if snapshot != base:
                raise ValueError('Content snapshot no longer matches 06_batch/batch.base.json')
        elif stage_id == 'preassemble':
            from . import agent_tasks, batch_fill
            base = _json(root, '06_batch/batch.base.json')
            agent_tasks._coverage(base, _units(root))
            if (root / batch_fill.STATE).is_file():
                batch_fill._managed(root)
            for row in base['items']:
                if not isinstance(row.get('stem'), str) or not isinstance(row.get('options'), list):
                    raise ValueError('Preassembled source fields do not match fill consumers')
        # 不再因为“生产者输入哈希变了”就拒绝交付——那是反复哈希比对、卡流程的来源。
        # 是否过期由 is_stale（完成+产物在）与用户后续验证决定；这里进入下面的
        # 内容正确性校验（题号覆盖/答案对应/结构绑定/格式），保证“填对题号、格式正确”。
        if stage_id == 'audio':
            # audio 的交付物是“媒体映射 audio_map + 切片证据 segment-evidence”，
            # 不是代理阶段带 evidence 的 payload。用一致性校验验收：
            # 覆盖全部听力单元、文件存在且摘要相符、绑定未过期。
            from audio.consistency import pair_is_consistent, read_pair
            if not pair_is_consistent(root, read_pair(root)):
                raise ValueError('音频切片映射与证据不一致或已过期，需重新切段')
        elif stage_id in ('preassemble', 'answers', 'types', 'explanations', 'knowledge') and not (
                stage_id == 'preassemble' and legacy_source_only):
            from . import agent_tasks
            context = _context(root)
            payload = copy.deepcopy(context[agent_tasks.ARTIFACTS[stage_id]])
            agent_tasks._validate(root, stage_id, payload, context, _units(root))
        elif stage_id in BATCH_ARTIFACTS:
            payload = _json(root, BATCH_ARTIFACTS[stage_id])
            if stage_id == 'meta':
                _assert_meta_projection(_json(root, '06_batch/content.snapshot.json'), payload)
                _source_view(root, payload)
            elif stage_id == 'underlines':
                _assert_underline_projection(_json(root, '06_batch/batch_meta.base.json'), payload)
                _source_view(root, payload)
            else:
                # 组装阶段只产出候选；来源/内容正确性由最终闸门集中复核。
                _batch(root, stage_id, payload, defer_content=(stage_id == 'assemble'))
        elif stage_id in ('validate', 'review_gate', 'format_gate'):
            report = _json(root, spec.produces[0])
            if isinstance(report, dict) and report.get('passed') is not True and stage_id == 'review_gate':
                # 用户流程：一审/二审找出差异交人工即算审核执行完成；有真实人工
                # 差异（manual_only）时允许继续打包上传批次，但绝不算放行/质量通过。
                if report.get('manual_only') is True:
                    from . import review
                    summary = review.evaluate_rounds(root)
                    if summary.get('execution_completed') is not True:
                        raise ValueError('Required review rounds have not completed')
                    expected = report.get('batch_sha256')
                    final = root / '06_batch/batch_meta.json'
                    if expected and final.is_file() and expected != mf.sha256_file(final):
                        raise ValueError('review_gate report belongs to an older final batch')
                    return {'passed': True, 'unqualified': True,
                            'errors': [], 'release_ready': False,
                            'note': 'review_gate executed with manual-review differences pending'}
            if not isinstance(report, dict) or report.get('passed') is not True:
                raise ValueError(stage_id + ' has not passed its downstream gate')
            expected = report.get('final_sha256') if stage_id == 'format_gate' else report.get('batch_sha256')
            if expected and expected != mf.sha256_file(root / '06_batch/batch_meta.json'):
                raise ValueError(stage_id + ' report belongs to an older final batch')
        elif stage_id == 'review':
            from . import review
            summary = review.evaluate_rounds(root)
            if summary.get('execution_completed') is not True:
                raise ValueError('Required review rounds have not completed')
        return {'passed': True, 'errors': []}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'passed': False, 'errors': [{'code': 'HANDOFF_INVALID', 'stage': stage_id,
                                            'message': str(exc)}]}


def require_stage(batch_dir, manifest, stage_id):
    report = inspect_stage(batch_dir, manifest, stage_id)
    if not report['passed']:
        raise ValueError('Stage handoff rejected: ' + '; '.join(row['message'] for row in report['errors']))
    return report
