"""Shared deterministic stage paths, before exporting or submitting model work."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import math
import os
from pathlib import Path


AUDIO_CANDIDATE_KEYS = ("unit_id", "file", "start", "end", "evidence")
AUDIO_REJECTED_QUALITY = ("micro", "interpolated", "coarse")


def audio_evidence_ready(batch_dir) -> bool:
    """Validate cutter evidence with the same context and contract as an import."""
    from . import agent_tasks
    batch = Path(batch_dir)
    path = batch / "02_audio" / "segment-evidence.json"
    if not path.is_file():
        return False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        items = document.get("items") if isinstance(document, dict) else None
        if not isinstance(items, list) or not items:
            return False
        if document.get("generator") == "audio.segment/v1":
            return False  # Legacy cuts used an overly broad verification rule.
        if any(item.get("verified") is not True
               or item.get("quality") in AUDIO_REJECTED_QUALITY
               or item.get("review_reasons") for item in items if isinstance(item, dict)):
            return False
        keys = ("unit_id", "file", "start", "end", "evidence")
        payload = {"items": [{key: item[key] for key in keys} for item in items
                             if isinstance(item, dict) and all(key in item for key in keys)]}
        if len(payload["items"]) != len(items):
            return False
        context, _, missing, units = agent_tasks._collect(batch, "audio")
        # Compare both sides under the prompt's host-path redaction rule.
        if missing or context.get('02_audio/segment-evidence.json') != agent_tasks._sanitized(document):
            return False
        agent_tasks._validate(batch, "audio", payload, context, units)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def _strict_audio_candidates() -> bool:
    """Strict mode keeps unverified candidate cuts out of the normal path."""
    return str(os.environ.get("WF_ACCEPT_AUDIO_CANDIDATES", "1")).strip().lower() in (
        "0", "false", "no", "off")


def audio_candidates_acceptable(batch_dir) -> bool:
    """Cuts are real but only their boundary is unverified.

    The audio contract itself accepts these candidate rows by default
    (``WF_ACCEPT_AUDIO_CANDIDATES`` != 0, see ``agent_tasks``): file and
    offsets are copied verbatim and the review reasons are carried into the
    delivery evidence. The deterministic preflight must therefore hand such a
    batch to the normal acceptance path instead of pinning the stage in
    ``needs_review`` forever — nothing in the workflow can promote a candidate
    row to ``verified`` afterwards. Structural damage (missing row fields,
    missing evidence, missing cut file, unusable bounds, rejected quality) is
    still rejected here, and strict mode restores the old needs_review gate.
    """
    if _strict_audio_candidates():
        return False
    batch = Path(batch_dir)
    path = batch / "02_audio" / "segment-evidence.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(document, dict):
        return False
    if document.get("generator") == "audio.segment/v1":
        return False  # Legacy cuts cannot be trusted as candidate rows either.
    items = document.get("items")
    if not isinstance(items, list) or not items:
        return False
    pending = False
    for item in items:
        if not isinstance(item, dict) or not all(key in item for key in AUDIO_CANDIDATE_KEYS):
            return False
        if not item.get("evidence"):
            return False
        if item.get("quality") in AUDIO_REJECTED_QUALITY:
            return False
        start, end = item.get("start"), item.get("end")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in (start, end)):
            return False
        if not 0 <= start < end:
            return False
        if not (batch / str(item["file"])).is_file():
            return False
        if item.get("verified") is not True or item.get("review_reasons"):
            pending = True
    return pending


@contextmanager
def _segment_snapshot_lock(batch):
    """Hold the cutter's lock without waiting behind a long transcription."""
    from . import filelock
    with filelock.try_file_lock(batch / '02_audio/.segment.lock') as acquired:
        yield acquired


def _audio(batch):
    if not (batch / '02_audio/segment-evidence.json').is_file():
        return None
    # A live cutter must keep ownership of the pair. Do not wait and thereby
    # delay other ready model tasks in this scheduler wave.
    with _segment_snapshot_lock(batch) as acquired:
        if not acquired:
            return 3, {'stage': 'audio', 'status': 'needs_review', 'completed': False,
                       'detail': '听力切段正在更新证据，完成后按当前输入复验', 'model_calls': 0}
        return _audio_locked(batch)


def _audio_locked(batch):
    from . import agent_protocol, agent_tasks, contracts, handoff, manifest as mf, runner
    from . import scoped_regeneration
    if not (batch / '02_audio/segment-evidence.json').is_file():
        return None
    # Keep validation and delivery in one exclusive section. In particular, do
    # not call agent._mark_stage_completed after releasing this lock: a cutter or
    # an upstream import could replace the inputs between the check and mark.
    with agent_protocol._batch_lock(batch, 1500):
        if scoped_regeneration.requested(batch, 'audio'):
            return None  # Scoped repairs must validate/merge the selected rows.
        path = batch / '02_audio/segment-evidence.json'
        if not path.is_file():
            return None
        detail = '现有切片边界证据不足；候选已保留。重新切段或核对边界，不再让模型重写证据。'
        try:
            manifest = mf.load(batch)
            spec = contracts.STAGE_BY_ID['audio']
            # An explicit repeat can revalidate an existing delivery. Upstream
            # checks still apply; only export's own-stage-complete guard differs.
            def blockers_for(state):
                stages = dict(state.get('stages', {}))
                stages['audio'] = dict(stages.get('audio', {}), status=mf.PENDING)
                return agent_protocol._preconditions(dict(state, stages=stages), spec)
            blockers = blockers_for(manifest)
            if blockers:
                raise ValueError('; '.join(blockers))
            _, hashes, missing, _ = agent_tasks._collect(batch, 'audio')
            if missing:
                raise ValueError('; '.join(missing))
            # Bind originals, cut bytes, evidence, rules and upstream decisions.
            # The segmenter also has its own lock, so detect uncoordinated file
            # replacement even while our workflow import lock is held.
            before = agent_protocol._fingerprints(batch, manifest, 'audio', hashes)
            outputs = mf.output_hashes(batch, spec)
            if not audio_evidence_ready(batch):
                if audio_candidates_acceptable(batch):
                    # 候选切片：切片文件与起止时间真实存在，只有边界不是真实静音/
                    # 精确对齐。audio 契约默认接受候选（WF_ACCEPT_AUDIO_CANDIDATES
                    # 缺省=1）并记录 review 说明，因此这里交回正常验收路径，而不是
                    # 把阶段钉死在 needs_review（候选行没有任何途径再变成 verified）。
                    # strict 模式（=0）仍由上面的契约判定拦截为 needs_review。
                    return None
                raise ValueError('切片证据未通过 audio 契约')
            checked = handoff.inspect_stage(batch, manifest, 'audio')
            if checked.get('passed') is not True:
                raise ValueError(json.dumps(checked, ensure_ascii=False))
            current = mf.load(batch)
            _, current_hashes, missing, _ = agent_tasks._collect(batch, 'audio')
            if (missing or blockers_for(current)
                    or before != agent_protocol._fingerprints(batch, current, 'audio', current_hashes)
                    or outputs != mf.output_hashes(batch, spec)):
                raise ValueError('音频验收期间输入或映射已变化，需按当前证据复验')
            if not runner._record_success(batch, current, spec):
                raise ValueError('音频交付未完成')
            current['status'] = mf.derive_status(current)
            mf.save(batch, current)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return 3, {'stage': 'audio', 'status': 'needs_review', 'completed': False,
                       'detail': detail, 'reason': str(exc), 'model_calls': 0}
        return 0, {'stage': 'audio', 'status': 'completed', 'completed': True,
                   'deterministic': True, 'model_calls': 0,
                   'detail': '切段产物已通过校验，未调用模型'}


def run(batch_dir, stage_id, *, dry_run=False):
    """Return (exit code, report) if handled, or None for normal model work.

    Answers require complete scoped originals and use the export/import transaction.
    Explanations retain their source-bound transaction including scoped fallback.
    Audio never invents a mapping from evidence.
    """
    if dry_run or stage_id not in ('answers', 'explanations', 'audio'):
        return None
    batch = Path(batch_dir).resolve()
    if stage_id == 'audio':
        return _audio(batch)
    if stage_id == 'answers':
        from . import answers_direct
        if answers_direct.build_payload(batch) is None:
            return None
        installed = answers_direct.install(batch)
        if installed is None:
            return None
        return 0, {'stage': 'answers', 'status': 'completed', 'completed': True,
                   'deterministic': True, 'model_calls': 0,
                   'detail': '完整原文答案已通过大题来源绑定并经通用协议导入'}
    from . import explanations_direct, scoped_regeneration
    scoped = scoped_regeneration.requested(batch, stage_id)
    if explanations_direct.build_payload(batch) is None:
        return None
    try:
        installed = explanations_direct.install(batch)
    except ValueError:
        if not scoped:
            raise
        return None
    if installed is None:
        return None
    return 0, {'stage': 'explanations', 'status': 'completed', 'completed': True,
               'deterministic': True, 'model_calls': 0,
               'detail': '原文直抄已通过来源绑定校验并经通用协议导入'}
