# -*- coding: utf-8 -*-
"""Read-only anomaly projection for paper cards and unit lists.

Distinguishes hard anomalies (RED) from ordinary label-review diffs (YELLOW)
without deleting or weakening any workflow validation rule:
- RED (anomaly): stage/validator failure on unit or batch, upload-format hard
  errors (Rule 17: errors only, never deferred/warnings), format_open findings,
  missing question type, or Round-1 content error/blocked verdict.
- YELLOW (needs_human): normal label/type/subtype review suggestions.
"""
from __future__ import annotations

import re
from pathlib import Path

from workflow import contracts as wf_contracts
from workflow.workflow_primitives import read_json_safe as load

_POST_FLOW_STAGES = frozenset({"upload_gate", "upload", "publish_gate"})
_STAGE_TITLES = {s.id: s.title for s in wf_contracts.STAGES} if wf_contracts else {}
_ONE_WORD_BLANK_CUE = re.compile(r"每空一词|每空仅填一词")
_BLANK_COUNT_CONFLICT_CUE = re.compile(
    r"空数|占一空|一个空|单空|仅给两段|填入一个空|每空.*(?:冲突|矛盾)|(?:冲突|矛盾).*每空"
)
_GENERAL_FORMAT_ONLY_CUE = re.compile(
    r"(?:全角|半角|标点|空格|缩进|对齐|换行|断行|连字符|下划线(?:长度|连排|偏移)|横线(?:长度|连排|未断开)|加粗(?:范围|偏移)|排版(?:格式|瑕疵|问题)|格式(?:不规范|瑕疵|问题))"
)
_SUBSTANTIVE_ERROR_CUE = re.compile(
    r"答案错误|答案有误|答案不符|答案与原卷|答案缺失|无答案|漏题|缺题干|缺选项|缺材料|缺失题干|缺失选项|缺失材料|错别字|乱码|截断|串题|原卷缺失|图片缺失|题型错误|无法作答"
    # 答案语义/词形类实质错误：同一句即使夹带格式描述也必须标红。
    r"|词形|单复数|时态|答案少词|答案多词|少一词|多一词|漏词|拼写|语法形式|空位数量不确定|空位数量不符|空位数不确定|空位数不符"
)
# 去掉格式描述后，剩余文字仍带“错/不符/缺/漏/不确定”等判断词，即视为夹带实质问题。
_RESIDUAL_ERROR_CUE = re.compile(r"错|误|不符|不一致|不对|缺|漏|无法|不确定|串|冲突|矛盾")
_FORMAT_NOISE = re.compile(_GENERAL_FORMAT_ONLY_CUE.pattern + r"|排版|格式|不规范|瑕疵")
_ONE_WORD_NOISE = re.compile(
    _BLANK_COUNT_CONFLICT_CUE.pattern + "|" + _ONE_WORD_BLANK_CUE.pattern + "|" + _FORMAT_NOISE.pattern + r"|冲突|矛盾"
)
# 横线连排/合并会改变可见空位数量，只有结构证据成立时才算原稿排版瑕疵。
_MERGED_LINE_CUE = re.compile(r"连排|连成|未断开|合并|一条长")
_UNDERLINE_TAG = re.compile(r"<u>([\s\u3000_]*)</u>", re.I)
_UNDERSCORE_RUN = re.compile(r"_{2,}")
_ANSWER_WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
# 原稿一词一空约 8 个下划线；连排后总长至少按每词 6 个计，才认定这条长横线能容纳全部答案词。
_MIN_UNDERLINE_PER_WORD = 6


def _blank_runs(stem: str) -> list[int]:
    """题干里每段作答横线的长度：<u>…</u> 按内部字符数，裸下划线按连续长度。"""
    runs: list[int] = []

    def _tag(match: re.Match) -> str:
        runs.append(max(len(match.group(1)), 1))
        return " "

    rest = _UNDERLINE_TAG.sub(_tag, str(stem or ""))
    runs.extend(len(m.group(0)) for m in _UNDERSCORE_RUN.finditer(rest))
    return runs


def _answer_text(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value if v is not None)
    return str(value or "")


def _stem_answer_pairs(source: object) -> list[tuple[str, str]]:
    """来源里可用于结构判断的（题干, 答案）对：小题优先，其次单元自身。"""
    if not isinstance(source, dict):
        return []
    pairs: list[tuple[str, str]] = []
    for sub in source.get("subquestions") or []:
        if not isinstance(sub, dict):
            continue
        stem, answer = str(sub.get("stem") or ""), _answer_text(sub.get("answer"))
        if stem.strip() and answer.strip():
            pairs.append((stem, answer))
    if not pairs:
        stem, answer = str(source.get("stem") or ""), _answer_text(source.get("answer"))
        if stem.strip() and answer.strip():
            pairs.append((stem, answer))
    return pairs


def _merged_blank_evidence(*sources: object) -> bool:
    """结构上能否证明“每空一词”空数冲突只是原稿把多段横线连排成一条。

    取第一个带题干+答案的来源（列表行优先，其次批次条目）：至少一个小题横线段数少于答案词数
    且横线总长足以容纳全部词，才算连排；任一小题段数少于词数但横线长度不足，即为真实空位冲突。
    拿不到结构证据时一律不豁免（保守标红）。
    """
    for source in sources:
        pairs = _stem_answer_pairs(source)
        if not pairs:
            continue
        merged = False
        for stem, answer in pairs:
            runs = _blank_runs(stem)
            words = len(_ANSWER_WORD.findall(answer))
            if not runs or words <= len(runs):
                continue
            if sum(runs) < words * _MIN_UNDERLINE_PER_WORD:
                return False
            merged = True
        return merged
    return False


def _is_source_format_only_complaint(row: dict, detail: str, batch_item: object = None) -> bool:
    """原稿固有排版/格式瑕疵不算实质内容异常；判定口径从严，宁可标红也不吞错。

    - 命中实质错误词（漏题、答案不符、缺题干/选项、词形/单复数/时态、答案少词/多词等）一律标红；
    - 去掉格式描述后剩余文字仍带“错/不符/缺/漏/不确定”等判断词，同样标红；
    - “每空一词”空数冲突与横线连排类抱怨会改变空位数量，必须有横线结构证据
      （见 _merged_blank_evidence）才不标红。batch_item 可为批次条目或 unit_id -> 条目 的惰性读取函数。
    """
    text = str(detail or "").strip()
    if not text or _SUBSTANTIVE_ERROR_CUE.search(text):
        return False

    def _item() -> object:
        if callable(batch_item):
            return batch_item(str((row or {}).get("unit_id") or "").strip())
        return batch_item

    if _ONE_WORD_BLANK_CUE.search(text) and _BLANK_COUNT_CONFLICT_CUE.search(text):
        if _RESIDUAL_ERROR_CUE.search(_ONE_WORD_NOISE.sub(" ", text)):
            return False
        return _merged_blank_evidence(row, _item())
    if not _GENERAL_FORMAT_ONLY_CUE.search(text):
        return False
    if _RESIDUAL_ERROR_CUE.search(_FORMAT_NOISE.sub(" ", text)):
        return False
    if _MERGED_LINE_CUE.search(text):
        return _merged_blank_evidence(row, _item())
    return True


def load_preview_batch_items(folder: Path) -> list[dict]:
    """Read-only fallback item loader so mid-stage stuck batches still show rows.

    Checks formal batch files first, then preassemble/draft/confirmed structure.
    Overlays 03_types/types.json when draft items have not merged types yet.
    Never mutates any file on disk.
    """
    candidates = (
        ("06_batch/batch.json", "items"),
        ("06_batch/batch_meta.json", "items"),
        ("06_batch/batch.base.json", "items"),
        ("06_batch/question-draft.json", "items"),
        ("01_source/structure.confirmed.json", "units"),
    )
    raw_items: list[dict] = []
    source_rel = ""
    for rel, key in candidates:
        path = folder / rel
        if not path.is_file():
            continue
        data = load(path, None)
        items = data if isinstance(data, list) else (data.get(key) or data.get("items") or data.get("units") if isinstance(data, dict) else None)
        if isinstance(items, list) and items:
            raw_items = [dict(x) for x in items if isinstance(x, dict)]
            source_rel = rel
            break
    if not raw_items:
        return []

    need_types = any(not str(it.get("type") or it.get("question_type") or "").strip() for it in raw_items)
    types_by_uid: dict[str, dict] = {}
    if need_types:
        tdata = load(folder / "03_types/types.json", {}) or {}
        for entry in (tdata.get("items") or []) if isinstance(tdata, dict) else []:
            if isinstance(entry, dict) and entry.get("unit_id"):
                types_by_uid[str(entry["unit_id"])] = entry

    normalized: list[dict] = []
    for idx, item in enumerate(raw_items):
        uid = str(item.get("unit_id") or item.get("id") or "").strip()
        if uid and uid in types_by_uid:
            t_entry = types_by_uid[uid]
            if not str(item.get("type") or item.get("question_type") or "").strip() and t_entry.get("type"):
                item["type"] = t_entry["type"]
            if not item.get("subtypes") and isinstance(t_entry.get("subtypes"), list):
                item["subtypes"] = t_entry["subtypes"]
        # Ensure structure.confirmed.json unnumbered units still produce a display row.
        if source_rel == "01_source/structure.confirmed.json":
            nums = item.get("numbers")
            if isinstance(nums, list) and len(nums) == 0:
                item.setdefault("printed_numbers", [])
                item["numbers"] = [idx + 1]
                item.setdefault("number", idx + 1)
        normalized.append(item)
    return normalized


def _clean_reason(text: object, limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


def _latest_stage_repair_failures(folder: Path, stage_id: str) -> dict[str, str]:
    """Read unit-scoped errors from the latest agent-run repair-progress of stage_id."""
    runs_dir = folder / "workflow" / "agent-runs"
    if not runs_dir.is_dir():
        return {}
    prefix = f"{stage_id}-"
    run_dirs = [
        p for p in runs_dir.iterdir()
        if p.is_dir() and p.name.startswith(prefix) and p.name != f"{stage_id}-parts"
    ]
    if not run_dirs:
        return {}
    run_dirs.sort(key=lambda p: p.stat().st_mtime_ns if p.exists() else 0, reverse=True)
    latest = run_dirs[0]
    progress_files = []
    if (latest / "repair-progress.json").is_file():
        progress_files.append(latest / "repair-progress.json")
    numbered = sorted(
        (p for p in latest.glob("repair-progress-*.json") if re.fullmatch(r"repair-progress-\d+\.json", p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    progress_files.extend(numbered)
    for pfile in progress_files:
        data = load(pfile, None)
        if not isinstance(data, dict):
            continue
        failed = data.get("failed_units")
        det = data.get("deterministic_failures")
        result: dict[str, str] = {}
        if isinstance(failed, dict):
            for uid, err in failed.items():
                if uid and err:
                    result[str(uid)] = _clean_reason(err)
        if isinstance(det, dict):
            for uid, info in det.items():
                if uid and str(uid) not in result:
                    diag = info.get("diagnostics") if isinstance(info, dict) else info
                    result[str(uid)] = _clean_reason(diag or "确定性校验未通过")
        if result:
            return result
    return {}


def collect_unit_stage_anomalies(
    folder: Path,
    manifest: dict | None,
    diagnostics: dict | None,
    rows: list[dict],
) -> tuple[dict[str, list[str]], list[str], bool]:
    """Collect unit-scoped and batch-scoped hard errors without mutating inputs."""
    unit_errors: dict[str, list[str]] = {}
    batch_reasons: list[str] = []
    stage_blocked = False

    def add_unit_error(uid: str, msg: str) -> None:
        key = str(uid or "").strip()
        clean = _clean_reason(msg)
        if not key or not clean:
            return
        bucket = unit_errors.setdefault(key, [])
        if clean not in bucket:
            bucket.append(clean)

    def add_batch_reason(msg: str) -> None:
        clean = _clean_reason(msg)
        if clean and clean not in batch_reasons:
            batch_reasons.append(clean)

    has_scoped = any(str(r.get("identity_version") or "") == "scoped-v1" for r in rows)
    uid_by_number: dict[str, str] = {}
    uid_by_index: dict[int, str] = {}
    for idx, r in enumerate(rows):
        uid = str(r.get("unit_id") or r.get("number") or "").strip()
        if uid:
            uid_by_index[idx] = uid
        if not has_scoped and uid:
            for n in (r.get("numbers") or [r.get("number")]):
                if str(n or "").strip():
                    uid_by_number[str(n).strip()] = uid

    diag_by_stage: dict[str, dict] = {}
    if isinstance(diagnostics, dict):
        for item in diagnostics.get("items") or []:
            if isinstance(item, dict) and item.get("stage"):
                diag_by_stage[str(item["stage"])] = item

    stages = (manifest or {}).get("stages") or {}
    for sid, record in stages.items():
        if not isinstance(record, dict) or sid in _POST_FLOW_STAGES:
            continue
        status = str(record.get("status") or "pending")
        # Rule 6: review_gate == needs_review is normal yellow diff output, not an anomaly.
        if sid == "review_gate" and status == "needs_review":
            continue
        if status not in ("failed", "blocked"):
            continue
        stage_blocked = True
        title = _STAGE_TITLES.get(sid, sid)
        diag = diag_by_stage.get(sid) or {}
        reason = _clean_reason(diag.get("reason") or record.get("error") or record.get("notes") or "阶段未通过")
        add_batch_reason(f"{title}：{reason}" if reason and reason != "阶段失败" else f"{title}未通过")

        diag_failed = diag.get("failed_units")
        if isinstance(diag_failed, dict):
            for uid, uerr in diag_failed.items():
                add_unit_error(str(uid), f"{title}未过：{_clean_reason(uerr)}")
        for uid, uerr in _latest_stage_repair_failures(folder, sid).items():
            add_unit_error(uid, f"{title}未过：{uerr}")

    # Validation report hard errors (if validate ran and recorded errors).
    val_stage = stages.get("validate") if isinstance(stages.get("validate"), dict) else {}
    val_report = load(folder / "07_review/validation-report.json", None)
    if isinstance(val_report, dict) and (
        val_report.get("passed") is False or str(val_stage.get("status") or "") in ("failed", "blocked")
    ):
        for key in ("errors", "blocking", "issues"):
            entries = val_report.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    severity = str(entry.get("severity") or "error").lower()
                    if key == "issues" and severity in ("warn", "warning", "info"):
                        continue
                    msg = _clean_reason(entry.get("message") or entry.get("reason") or entry.get("code") or "本地体检未通过")
                    uid = str(entry.get("unit_id") or "").strip()
                    if not uid and not has_scoped and entry.get("number") is not None:
                        uid = uid_by_number.get(str(entry.get("number")).strip(), "")
                    if uid:
                        add_unit_error(uid, f"本地体检未过：{msg}")
                    else:
                        add_batch_reason(f"本地体检：{msg}")
                elif isinstance(entry, str) and entry.strip():
                    add_batch_reason(f"本地体检：{_clean_reason(entry)}")

    # Per-unit quarantined anomalies from 07_review/unit-anomalies.json (downstream stages still ran).
    anomalies_doc = load(folder / "07_review/unit-anomalies.json", None)
    if isinstance(anomalies_doc, dict) and isinstance(anomalies_doc.get("units"), dict):
        for uid, entries in anomalies_doc["units"].items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                sid = str(entry.get("stage") or "").strip()
                title = _STAGE_TITLES.get(sid, sid or "阶段")
                err = _clean_reason(entry.get("error") or "校验未通过")
                add_unit_error(str(uid), f"{title}异常：{err}")

    # Upload format gate hard errors (Rule 17: ONLY .errors, NEVER deferred/warnings).
    fmt_report = load(folder / "07_review/upload-format.json", None)
    if isinstance(fmt_report, dict):
        fmt_errors = fmt_report.get("errors")
        if isinstance(fmt_errors, list) and fmt_errors:
            stage_blocked = True
            add_batch_reason(f"最终格式检查 {len(fmt_errors)} 项硬错误")
            for entry in fmt_errors:
                if not isinstance(entry, dict):
                    continue
                msg = _clean_reason(entry.get("message") or entry.get("reason") or entry.get("code") or "上传格式错误")
                uid = str(entry.get("unit_id") or "").strip()
                if not uid and isinstance(entry.get("index"), int):
                    uid = uid_by_index.get(entry["index"], "")
                if not uid and not has_scoped and entry.get("number") is not None:
                    uid = uid_by_number.get(str(entry.get("number")).strip(), "")
                if uid:
                    add_unit_error(uid, f"格式硬错误：{msg}")

    return unit_errors, batch_reasons, stage_blocked


def decorate_anomaly_rows(
    folder: Path,
    rows: list[dict],
    *,
    manifest: dict | None = None,
    diagnostics: dict | None = None,
    advance: dict | None = None,
    precollected: tuple[dict[str, list[str]], list[str], bool] | None = None,
) -> dict:
    """Mutate each row in-place with red anomaly flags and return batch anomaly summary."""
    if manifest is None:
        manifest = load(folder / "workflow/manifest.json", {}) or {}
    if precollected is not None:
        unit_errors, batch_reasons, stage_blocked = precollected
    else:
        unit_errors, batch_reasons, stage_blocked = collect_unit_stage_anomalies(folder, manifest, diagnostics, rows)

    adv = advance or {}
    adv_active = bool(adv.get("running") or adv.get("queued") or adv.get("waiting"))
    if not adv_active and (folder / "workflow/activity.json").is_file():
        try:
            from workflow import activity
            adv_active = bool(activity.read(folder))
        except (ImportError, OSError, ValueError):
            adv_active = False

    stage_blocked_idle = bool(stage_blocked and not adv_active)

    batch_items_by_uid: dict[str, dict] | None = None

    def batch_item_for(unit_id: str) -> dict:
        """按 unit_id 惰性读取批次条目（只在需要横线结构证据时读盘一次）。"""
        nonlocal batch_items_by_uid
        if batch_items_by_uid is None:
            batch_items_by_uid = {}
            try:
                items = load_preview_batch_items(folder)
            except (OSError, ValueError, TypeError):
                items = []
            for it in items:
                key = str(it.get("unit_id") or it.get("id") or "").strip()
                if key:
                    batch_items_by_uid.setdefault(key, it)
        if not unit_id:
            return {}
        return batch_items_by_uid.get(unit_id) or {}

    anomaly_unit_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("excluded"):
            row["is_anomaly"] = False
            row["anomaly_reasons"] = []
            row["anomaly_summary"] = ""
            continue
        uid = str(row.get("unit_id") or row.get("number") or "").strip()
        reasons: list[str] = []

        def push(msg: str) -> None:
            clean = _clean_reason(msg)
            if clean and clean not in reasons:
                reasons.append(clean)

        for err in unit_errors.get(uid, ()):
            push(err)

        if not str(row.get("type") or "").strip():
            push("题型缺失（未判定）")

        if int(row.get("format_count") or 0) > 0:
            push(f"存在 {int(row.get('format_count'))} 项内容/格式异常")

        if (
            str(row.get("state") or "") == "blocked"
            or str(row.get("verdict") or "") == "error"
            or str(row.get("severity") or "") == "blocker"
        ):
            raw_summary = str(row.get("open_summary") or "")
            detail = _clean_reason(raw_summary)
            if (
                str(row.get("state") or "") != "blocked"
                and str(row.get("severity") or "") != "blocker"
                and _is_source_format_only_complaint(row, raw_summary, batch_item=batch_item_for)
            ):
                pass
            else:
                push(f"一审内容判错：{detail}" if detail else "一审判定存在阻断/内容错误")

        row["is_anomaly"] = bool(reasons)
        row["anomaly_reasons"] = reasons
        row["anomaly_summary"] = "；".join(reasons)
        if reasons:
            anomaly_unit_count += 1

    has_anomaly = bool(anomaly_unit_count > 0 or stage_blocked_idle or batch_reasons)
    anomaly_count = anomaly_unit_count if anomaly_unit_count > 0 else (len(batch_reasons) or (1 if stage_blocked_idle else 0))
    summary_parts: list[str] = []
    if anomaly_unit_count > 0:
        summary_parts.append(f"{anomaly_unit_count} 题异常")
    for br in batch_reasons[:2]:
        if br not in summary_parts:
            summary_parts.append(br)
    return {
        "has_anomaly": has_anomaly,
        "anomaly_count": anomaly_count,
        "anomaly_unit_count": anomaly_unit_count,
        "stage_blocked": stage_blocked_idle,
        "anomaly_reasons": batch_reasons,
        "anomaly_summary": " · ".join(summary_parts),
    }
