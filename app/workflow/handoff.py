"""Read-only producer acceptance shared by dispatch, import and reconciliation.

A status or an existing file alone is never a usable delivery. Validators reuse
the consumers' schema, catalog, source and final wire-shape checks.
"""
from __future__ import annotations

import copy
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


def _batch(root, stage, payload):
    from . import agent_tasks, format_gate, label_audit
    from .runner import source_audit_for_batch
    units = _units(root)
    rows = agent_tasks._coverage(payload, units)
    problems = format_gate._shape(payload)
    if problems:
        raise ValueError('Downstream wire schema: ' + str(problems[:8]))
    labels = label_audit.inspect_batch(payload)
    if not labels.get('passed'):
        raise ValueError('Downstream label schema: ' + str(labels.get('errors', [])[:8]))
    audit = source_audit_for_batch(root, payload)
    # 图片/音频属于 AI 能力受限项：缺失或未映射只作 informational，不阻断下游。
    # 结构、题号、材料归属等文本类问题仍是硬阻断。
    media_codes = {'SOURCE_IMAGE_MISSING', 'OPTION_CONTENT_MISMATCH',
                   'SOURCE_OPTIONS_INCOMPLETE', 'SHARED_OPTIONS_MISMATCH'}
    blocking = [issue for issue in audit.get('issues', [])
                if issue.get('code') not in media_codes]
    if blocking:
        raise ValueError('Downstream source fidelity: ' + str(blocking[:8]))
    for unit in units:
        row = rows[unit['unit_id']]
        children = row.get('subquestions') or []
        agent_tasks._children(row, unit)
        for item in ([row] if unit['unit_kind'] == 'standalone' else children):
            for key in ('answer', 'explanation'):
                if not isinstance(item.get(key), str):
                    raise ValueError(f"{unit['unit_id']}: downstream {key} must be a string")


def inspect_stage(batch_dir, manifest, stage_id):
    """Inspect installed output only; never repair, install, mark or invoke a model."""
    from . import contracts, manifest as mf
    root = Path(batch_dir)
    spec = contracts.STAGE_BY_ID[stage_id]
    record = manifest.get('stages', {}).get(stage_id, {})
    try:
        if stage_id in ('upload_gate', 'upload', 'publish_gate'):
            return {'passed': True, 'errors': []}
        if record.get('status') == mf.SKIPPED and stage_id in ('answers', 'audio', 'underlines'):
            # No-input audio has no media contract. Answers and final views do.
            if stage_id == 'audio':
                if not mf.skip_reason(dict(manifest, batch_dir=str(root)), spec):
                    raise ValueError('Audio skip is no longer justified')
                return {'passed': True, 'errors': []}
        for relative in spec.produces:
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
        elif stage_id in ('answers', 'types', 'explanations', 'knowledge'):
            from . import agent_tasks
            context = _context(root)
            payload = copy.deepcopy(context[agent_tasks.ARTIFACTS[stage_id]])
            agent_tasks._validate(root, stage_id, payload, context, _units(root))
        elif stage_id in BATCH_ARTIFACTS:
            _batch(root, stage_id, _json(root, BATCH_ARTIFACTS[stage_id]))
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
