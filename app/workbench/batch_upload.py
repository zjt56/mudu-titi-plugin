# -*- coding: utf-8 -*-
"""Cloud question-bank upload orchestration, media staging & status projection.

Extracted from app/workbench/server.py to keep the HTTP server module focused.
All public symbols are re-exported by workbench.server for full compatibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from time import time

from workflow.workflow_primitives import read_json_safe as load


def _srv():
    from . import server
    return server


def _run_runner(args):
    srv = _srv()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-m", "workflow.runner", *args],
                          cwd=str(srv.ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800,
                          env=env)


def _prepare_failure_summary(report):
    """Summarize blocking findings without letting advisory warnings bury them."""
    if not isinstance(report, dict):
        return ""
    errors = report.get("errors") or ([report["error"]] if report.get("error") else [])
    if not isinstance(errors, list):
        errors = [errors]
    groups = {}
    for item in errors:
        if isinstance(item, dict):
            message = str(item.get("message") or item.get("code") or "上传预检错误")
            code = str(item.get("code") or "")
            field = str(item.get("field") or "")
            unit = str(item.get("unit_id") or "")
        else:
            message, code, field, unit = str(item), "", "", ""
        key = (code, message, field)
        group = groups.setdefault(key, {"count": 0, "units": []})
        group["count"] += 1
        if unit and unit not in group["units"] and len(group["units"]) < 3:
            group["units"].append(unit)
    lines = [f"上传准备未通过：{len(errors)} 项阻断错误"] if errors else ["上传预检未通过"]
    for (code, message, field), group in groups.items():
        detail = message + (f" [{code}]" if code and code != message else "")
        detail += f"（{group['count']} 项"
        if field:
            detail += f"，字段：{field}"
        if group["units"]:
            detail += "，示例单元：" + "、".join(group["units"])
        lines.append("- " + detail + "）")
    warnings = report.get("warnings")
    content = report.get("content_source")
    content_warnings = content.get("warnings") if isinstance(content, dict) else None
    count = (len(warnings) if isinstance(warnings, list) else 0) + (len(content_warnings) if isinstance(content_warnings, list) else 0)
    if count:
        lines.append(f"另有 {count} 项非阻断警告。")
    return "\n".join(lines)


def _validate_ever_passed(validate_stage, manifest_mod=None):
    """Return True if validate is completed or was invalidated after passing."""
    from workflow.execution_state import validate_ever_passed
    return validate_ever_passed(validate_stage)


def _check_upload_preconditions(folder, unreviewed_revision, attempt_token):
    """Check existing evidence before any remote media is written."""
    srv = _srv()
    from workflow import manifest, runner
    from workflow.label_audit import inspect_file
    state = manifest.load(folder)
    summary = runner.unreviewed_summary(folder, state)
    # 用户明确要求：未完成审核 / 有审核差异 都不阻碍上传到题库服务器。
    # 只在“已带确认令牌但审核revision变了”时提示重新确认，不做硬拦截。
    if attempt_token and (not unreviewed_revision or summary.get('revision') != unreviewed_revision):
        raise ValueError('审核状态已变化，请重新查看并确认上传')
    # 用户要求：内容过期/变化（is_stale）或人工改解析导致下游失效不阻碍上传——
    # 只要 validate 曾通过（completed，或从 completed 被标为 invalidated）即可上传；
    # 标签/结构等“内容对不对”的硬校验仍在下面执行。
    if not srv._validate_ever_passed(state.get('stages', {}).get('validate'), manifest):
        raise ValueError('批次校验未通过：请先完成本地体检（validate）')
    labels = inspect_file(folder / '06_batch' / 'batch_meta.json')
    if not labels.get('passed'):
        raise ValueError('批次标签校验未通过，请先检查标签')
    flow = runner.flowchart_checks(folder, state)
    if not flow.get('passed'):
        first = next(iter(flow.get('errors') or []), {})
        raise ValueError('试卷结构校验未通过：' + str(first.get('message') or '请检查原卷'))


def _stage_upload_media(folder):
    """Stage only media referenced by a stable final batch, after the upload click."""
    srv = _srv()
    from workflow import media_inventory, media_transfer, upload
    from workflow.local_agent import _batch_lock

    source = folder / '06_batch' / 'batch_meta.json'
    if not source.exists():
        return None
    # Serialize with workflow imports. Workbench and independent external edits
    # are detected by the stable reads and digest comparisons below.
    with _batch_lock(folder, 1500):
        root_fd = media_transfer._open_root(folder)
        try:
            raw = media_transfer._read_file(root_fd, '06_batch/batch_meta.json')
        finally:
            media_transfer._close_root(root_fd)
        from workflow.agent_protocol import _load
        metadata = _load(raw)
        # 若媒体已按内容寻址部署（07_upload/media-map.json 存在且覆盖批次全部附件
        # 文件名），直接复用该映射，避免重复 SSH 部署；也避免 referenced_media 对
        # 已部署 flat-hash 附件（/source-media/<sha>-…，非批次目录名前缀）误报
        # “Attachment URL is not owned by this batch”。仅当映射完整覆盖时复用。
        existing_map = folder / '07_upload' / 'media-map.json'
        if existing_map.is_file():
            try:
                deployed = json.loads(existing_map.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                deployed = None
            if isinstance(deployed, dict):
                needed = set()

                def _collect(node):
                    if isinstance(node, dict):
                        for a in (node.get('attachments') or []):
                            if isinstance(a, dict) and a.get('name'):
                                needed.add(a['name'])
                        for c in (node.get('subquestions') or []):
                            _collect(c)

                for item in (metadata.get('items') or []):
                    _collect(item)
                if needed and all(
                        isinstance(deployed.get(n), str)
                        and deployed[n].startswith('/source-media/')
                        for n in needed):
                    # 与正常路径保持同构：(mapping, source_hash)。缺少元组会导致
                    # 调用方 `mapping, source_hash = staged` 解包失败。
                    return {n: deployed[n] for n in needed}, hashlib.sha256(raw).hexdigest()
        refs = media_inventory.referenced_media(folder, metadata)
        if not refs:
            return None
        destination = media_transfer._settings()
        if destination[0] != 'ubuntu@101.43.1.57':
            raise ValueError('此 SSH 目标没有经过验证的媒体目录，无法自动暂存媒体')
        batch_id = metadata.get('batch_id') or srv.wf_manifest.load(folder)['workflow_id']
    # Do not hold the UI/workflow locks while SSH is transferring files: status
    # requests must remain responsive. Revalidate both metadata and every file.
    mapping = media_transfer.stage_media(folder, refs,
        static_root='/home/ubuntu/mudu-quiz/static/source-media', batch_id=batch_id,
        settings=destination)
    with _batch_lock(folder, 1500):
        if media_inventory.referenced_media(folder, metadata) != refs:
            raise ValueError('批次媒体在暂存期间发生变化，请重新上传')
        root_fd = media_transfer._open_root(folder)
        try:
            if media_transfer._read_file(root_fd, '06_batch/batch_meta.json') != raw:
                raise ValueError('正式批次在媒体暂存期间发生变化，请重新上传')
        finally:
            media_transfer._close_root(root_fd)
        upload._write(upload._path(folder, 'media-map.json'), upload._bytes(mapping))
        return mapping, hashlib.sha256(raw).hexdigest()


def _run_batch_upload(name, actor, unreviewed_revision='', attempt_token=''):
    srv = _srv()
    job = srv._upload_jobs[name]

    def step(label, args):
        job["step"] = label
        proc = srv._run_runner(args)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        job["output"] = out[-4000:]
        return proc.returncode, out, proc.stdout or ""

    try:
        folder = srv.batch_dir(name)
        job["step"] = "检查连接，必要时启动 SSH 隧道"
        from workbench import ssh_tunnel
        job["output"] = ssh_tunnel.ensure_upload_connection()
        job['step'] = '检查批次与确认状态'
        srv._check_upload_preconditions(folder, unreviewed_revision, attempt_token)
        job["step"] = "暂存引用的媒体"
        staged = srv._stage_upload_media(folder)
        preflight_path = Path(folder) / "07_upload" / "preflight.json"
        try:
            preflight_before = preflight_path.stat().st_mtime_ns
        except OSError:
            preflight_before = None

        def _fresh_preflight():
            try:
                if preflight_path.stat().st_mtime_ns == preflight_before:
                    return None
            except OSError:
                return None
            data = load(preflight_path)
            if isinstance(data, dict) and (data.get("passed") is False or data.get("errors")):
                return data
            return None

        if staged:
            from workflow import upload as upload_flow
            mapping, source_hash = staged
            job["step"] = "准备上传（冻结请求）"
            try:
                report = upload_flow.prepare(folder, media_map=mapping)
            except Exception:
                fresh = _fresh_preflight()
                if fresh is not None:
                    job.update(ok=False, error=srv._prepare_failure_summary(fresh), output="")
                    return
                raise
            if report.get('source_sha256') != source_hash:
                raise ValueError('正式批次在媒体暂存后发生变化，请重新上传')
            rc = 0 if report.get('passed') is True else 1
            stdout = json.dumps(report, ensure_ascii=False)
            out = stdout
            job["output"] = out[-4000:]
        else:
            rc, out, stdout = step("准备上传（冻结请求）", ["prepare-upload", "--batch-dir", str(folder)])
        if rc != 0:
            try:
                report = json.loads(stdout)
            except (ValueError, TypeError):
                report = None
            if not (isinstance(report, dict) and (report.get("passed") is False or report.get("errors"))):
                report = _fresh_preflight()
            if isinstance(report, dict) and (report.get("passed") is False or report.get("errors")):
                job.update(ok=False, error=srv._prepare_failure_summary(report), output="")
            else:
                job.update(ok=False, error="上传准备未通过：" + (out.splitlines()[-1] if out else str(rc)))
            return
        mark_args = ["mark", "--batch-dir", str(folder), "--stage", "upload_gate",
                     "--status", "approved", "--actor", actor,
                     "--note", "通过插件「上传批次」按钮确认" + ("（含未审核内容）" if attempt_token else "")]
        if attempt_token:
            mark_args += ['--unreviewed-revision', unreviewed_revision, '--unreviewed-attempt', attempt_token]
        rc, out, _ = step("确认上传闸门", mark_args)
        if rc != 0:
            job.update(ok=False, error="上传确认失败：" + (out.splitlines()[-1] if out else str(rc)))
            return
        upload_args = ["upload", "--batch-dir", str(folder), "--yes"]
        if attempt_token:
            upload_args += ['--unreviewed-attempt', attempt_token]
        rc, out, _ = step("写入云端上传暂存", upload_args)
        if rc != 0:
            job.update(ok=False, error="上传未完成：" + (out.splitlines()[-1] if out else str(rc)))
            return
        job.update(ok=True, step="已上传")
    except Exception as exc:  # noqa: BLE001 - 后台任务，错误回显给面板
        job.update(ok=False, error=str(exc))
    finally:
        job["running"] = False
        job["finished_at"] = time()


def start_batch_upload_impl(name, actor="本机操作员", *, allow_unreviewed=False, unreviewed_revision=''):
    srv = _srv()
    folder = srv.batch_dir(name)  # 批次不存在直接抛错
    if type(allow_unreviewed) is not bool:
        raise ValueError('allow_unreviewed 必须是布尔值')
    if allow_unreviewed:
        from workflow.runner import unreviewed_summary
        current = unreviewed_summary(folder, srv.wf_manifest.load(folder))
        if not isinstance(unreviewed_revision, str) or not unreviewed_revision or current['revision'] != unreviewed_revision:
            raise ValueError('审核状态已变化，请重新查看并确认')
    elif unreviewed_revision:
        raise ValueError('未明确确认不能指定未审核版本')
    attempt_token = uuid.uuid4().hex if allow_unreviewed else ''
    existing = srv.upload_state(name)
    if existing.get('state') == 'unknown':
        raise ValueError('上次上传结果未知，请先在远端核实，不能重复上传')
    if existing.get('state') == 'accepted':
        raise ValueError('该批次已有上传暂存回执，请勿重复上传')
    with srv._lock:
        job = srv._upload_jobs.get(name)
        if job and job.get("running"):
            raise ValueError("该批次正在上传中，请稍候")
        srv._upload_jobs[name] = {"running": True, "ok": None, "step": "开始", "output": "",
                                  "error": "", "started_at": time()}
    threading.Thread(target=srv._run_batch_upload, args=(name, actor, unreviewed_revision, attempt_token), daemon=True).start()
    return {"started": True}


def _cached_unreviewed_summary(folder: Path) -> dict:
    """Cache unreviewed_summary by review input fingerprint so 3s polls stay sub-millisecond."""
    srv = _srv()
    cache_key = (str(folder), srv._review_inputs_key(folder))
    hit = srv._UNREVIEWED_SUMMARY_CACHE.get(cache_key)
    if hit is not None:
        return dict(hit)
    from workflow.runner import unreviewed_summary
    summary = unreviewed_summary(folder, srv.wf_manifest.load(folder))
    if isinstance(summary, dict):
        if len(srv._UNREVIEWED_SUMMARY_CACHE) >= 32:
            srv._UNREVIEWED_SUMMARY_CACHE.clear()
        srv._UNREVIEWED_SUMMARY_CACHE[cache_key] = dict(summary)
    return summary


def upload_state(name, *, include_unreviewed=True):
    """Project in-memory progress and durable upload evidence after a restart."""
    srv = _srv()
    folder = srv.batch_dir(name)
    with srv._lock:
        job = dict(srv._upload_jobs.get(name) or {})
    result = {"running": False, "ok": None, "step": "", "output": "", "error": "",
              "state": "idle", "receipt": None}
    result.update(job)
    if include_unreviewed:
        try:
            result['unreviewed'] = srv._cached_unreviewed_summary(folder)
            if result['unreviewed'].get('revision'):
                result['requires_unreviewed_confirmation'] = not result['unreviewed']['passed']
        except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
            result['unreviewed'] = {'passed': False, 'revision': '', 'counts': {},
                                    'errors': [{'message': str(exc)}]}
    receipt = load(folder / "07_upload" / "receipt.json")
    receipt = receipt if isinstance(receipt, dict) and receipt.get("id") else None
    attempts = []
    for path in (folder / "07_upload" / "attempts").glob("*.json"):
        item = load(path)
        if isinstance(item, dict):
            attempts.append(item)
    if not receipt:
        receipt = next((a.get("receipt") for a in attempts if a.get("state") == "accepted"
                        and isinstance(a.get("receipt"), dict) and a["receipt"].get("id")), None)
    result["receipt"] = receipt
    # A receipt is written before its attempt becomes accepted. It settles that
    # attempt, but cannot settle a different in-flight payload.
    uncertain = next((a for a in attempts if a.get("state") in ("submitting", "unknown")
                      and not (receipt and a.get("payload_sha256")
                               and a.get("payload_sha256") == receipt.get("payload_sha256"))), None)
    rejected = next((a for a in attempts if a.get("state") in ("rejected", "batch_conflict")), None)
    preflight = load(folder / "07_upload" / "preflight.json")
    if result["running"]:
        result["state"] = "running"
    elif uncertain:
        recoverable = bool(uncertain.get('key_binding') and uncertain.get('api_base'))
        result.update(state="failed" if recoverable else "unknown", ok=False)
        result["step"] = ("提交结果未知，可用原 Key 和冻结请求恢复" if recoverable
                          else result["step"] or "提交结果未知")
        result["error"] = result["error"] or str(uncertain.get("details") or uncertain.get("error_code")
                                                or "提交结果未知，请先在远端工作台核实")
    elif receipt:
        # A later preflight failure cannot erase evidence of an accepted upload.
        # Keep its diagnostic visible without enabling a duplicate submission.
        if receipt.get('state') in ('staged_attachments_pending', 'attachment_rejected'):
            result.update(state="failed", ok=False)
            result["step"] = "审核附件未追加完成"
            error = receipt.get('attachment_error') or {}
            result["error"] = str(error.get('details') or error.get('code')
                                  or "审核附件未追加完成，请重试或检查远端暂存")
        else:
            result.update(state="accepted", ok=True)
            result["step"] = "已暂存，等待远端确认"
    elif job.get("ok") is False:
        result["state"] = "failed"
    elif rejected:
        result.update(state="failed", ok=False)
        result["step"] = result["step"] or "上传被拒绝"
        result["error"] = result["error"] or str(rejected.get("details") or rejected.get("error_code")
                                                or "上传被拒绝")
    elif isinstance(preflight, dict) and preflight.get("passed") is False:
        result.update(state="failed", ok=False)
        result["step"] = result["step"] or "上传准备未通过"
        result["error"] = result["error"] or srv._prepare_failure_summary(preflight)
    elif job.get("ok") is True:
        result.update(state="unknown", ok=False, error="上传完成但未找到暂存回执，请先在远端核实")
    return result
