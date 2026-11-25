# -*- coding: utf-8 -*-
"""Audio segmentation & WhisperX bootstrap background job orchestration.

Extracted from app/workbench/server.py to keep the HTTP server module focused.
All public symbols are re-exported by workbench.server for full compatibility.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from time import time

# 后台听力任务稳定性的时间参数（秒）：心跳在 progress 里刷新。
_AUDIO_STALE_GRACE = 5             # 状态刚写入时的宽限，避免误报残留
_AUDIO_WATCHDOG_SECONDS = 900       # 看门狗：>15min 无心跳则把任务判失败并清理，避免永久卡住
_AUDIO_WATCHDOG_TICK = 30


def _srv():
    from . import server
    return server


def _audio_modules():
    from audio import bootstrap as audio_bootstrap
    from audio import config as audio_config
    from audio import segment as audio_segment
    return audio_config, audio_bootstrap, audio_segment


def audio_overview():
    srv = _srv()
    cfgmod, boot, _ = srv._audio_modules()
    cfg = cfgmod.load()
    data_dir = Path(cfgmod.data_dir(cfg))
    return {"ok": True, "config": cfg, "config_path": str(cfgmod.user_config_path()),
            "defaults_path": str(cfgmod.config_path()),
            "data_dir": str(data_dir), "venv_python": str(boot.venv_python(cfg)),
            "hf_endpoint": cfgmod.resolve_hf_endpoint(cfg),
            "python_version": sys.version.split()[0],
            "platform_key": boot.platform_key(),
            "vendor_python": bool(boot.vendor_python(cfg)),
            "vendor_ffmpeg": bool((boot.VENDOR_DIR / "ffmpeg" / boot.platform_key() / "bin").is_dir()),
            "ready": boot.ready(cfg),
            "ffmpeg": boot.tool_path("ffmpeg"), "ffprobe": boot.tool_path("ffprobe"),
            "busy": srv._audio_job["running"], "job": srv._audio_job["kind"]}


def audio_status(batch: str | None = None):
    srv = _srv()
    cfgmod, boot, _ = srv._audio_modules()
    cfg = cfgmod.load()
    state = boot.read_status(Path(cfgmod.data_dir(cfg)), batch)
    # 不覆盖 state["ok"]：它是后台任务自己的成功/失败标记。
    state.update({"request_ok": True, "ready": boot.ready(cfg), "busy": srv._audio_job["running"],
                  "job": srv._audio_job["kind"]})
    if batch:
        state["batch"] = batch
        if state.get("running") and time() - float(state.get("updated") or 0) > srv._AUDIO_STALE_GRACE \
                and not srv._audio_has_live_worker(batch):
            # 状态文件残留（进程已被杀、后端重启过）：界面不能再显示“正在运行”，
            # 否则用户会一直等一个不存在的任务。下次点切段/自动推进时会真正重跑。
            state["stale"] = True
            state["running"] = False
            state["detail"] = "切段进程已中断（状态为残留），可重新开始"
    return state


def _audio_batch_folder(batch: str | None) -> Path | None:
    """把批次名解析成在 workflows 下的真实目录；越界或不存在时返回 None。"""
    srv = _srv()
    if not batch:
        return None
    try:
        root = srv.WORKFLOWS.resolve()
        folder = (srv.WORKFLOWS / batch).resolve()
    except OSError:
        return None
    if folder.parent != root or not folder.is_dir():
        return None
    return folder


def _audio_has_live_worker(batch: str | None) -> bool:
    """切段任务是否真的还活着（用于区分“真在跑”与“进程已死的残留状态”）。

    状态文件会活过后端重启：进程被强杀时线程的 finally 不会执行，running=True
    会一直留着。因此不能用状态文件当存活证据，也不能依赖 `ps`（Windows 没有，
    旧实现在 Windows 上恒返回 True，导致僵死状态永远清不掉）。权威证据只有两个：
    本进程的后台任务仍在跑，或本批次的 02_audio/.segment.lock 仍被某个进程持有。
    """
    srv = _srv()
    if srv._audio_job["running"] and (not batch or srv._audio_job.get("batch") == batch):
        return True
    folder = srv._audio_batch_folder(batch)
    if folder is None:
        return False
    try:
        from audio.segment import lock_is_held
    except (ImportError, OSError):
        return True  # 探针不可用时保守等待，不误判重开
    try:
        return lock_is_held(folder)
    except OSError:
        return True


def _audio_reap_stale(kind: str, batch, data_dir: Path, boot) -> None:
    """清理“状态 running 但进程已死”的僵死任务，避免永久卡住无法重开。

    只要有存活证据（本进程任务或批次锁）就不动；没有就立即重置，不再等 10 分钟
    心跳——残留状态本身不是运行证据。
    """
    srv = _srv()
    state = boot.read_status(data_dir, batch)
    if not state.get("running"):
        return
    if srv._audio_has_live_worker(batch):
        return  # 有真实进程在跑，交给看门狗，不抢占
    boot.write_status(data_dir, batch=batch, job=state.get("job") or kind, running=False,
                      ok=False, phase="error",
                      detail="", error="切段任务已中断（无存活进程，状态文件为残留），已自动重置，可重跑")


def start_audio_job_impl(kind: str, options: dict) -> dict:
    srv = _srv()
    cfgmod, boot, segment_mod = srv._audio_modules()
    cfg = cfgmod.load()
    data_dir = Path(cfgmod.data_dir(cfg))
    batch = Path(options["folder"]).name if kind == "segment" and options.get("folder") else None
    if batch:
        srv._audio_reap_stale(kind, batch, data_dir, boot)
        if srv._audio_has_live_worker(batch):
            raise ValueError("该批次已有切段任务在运行，请等它结束")
    with srv._audio_job["lock"]:
        if srv._audio_job["running"]:
            raise ValueError("已有听力任务在运行，请等它结束后再试")
        srv._audio_job.update({"running": True, "kind": kind, "batch": batch})

    # 每个任务独立的“最后心跳”标记，供看门狗判断是否卡死。
    beat = {"at": time()}

    def worker():
        watchdog_stop = threading.Event()

        def watchdog():
            while not watchdog_stop.wait(srv._AUDIO_WATCHDOG_TICK):
                if time() - beat["at"] > srv._AUDIO_WATCHDOG_SECONDS:
                    try:
                        boot.write_status(data_dir, batch=batch, job=kind, running=False, ok=False,
                                          phase="error", detail="",
                                          error=f"切段任务超过 {srv._AUDIO_WATCHDOG_SECONDS}s 无进展，判定僵死并清理；可重跑")
                    except Exception:  # noqa: BLE001
                        pass
                    return

        threading.Thread(target=watchdog, daemon=True).start()
        try:
            def progress(phase, pct, detail):
                beat["at"] = time()
                boot.write_status(data_dir, batch=batch, job=kind, running=True, ok=None,
                                  phase=phase, pct=pct, detail=detail, error="")
            beat["at"] = time()
            boot.write_status(data_dir, batch=batch, job=kind, running=True, ok=None,
                              phase="start", pct=0, detail="任务开始", error="")
            if kind == "bootstrap":
                boot.bootstrap(cfg, force=bool(options.get("force")))
                boot.write_status(data_dir, batch=batch, job=kind, running=False, ok=True,
                                  phase="done", pct=100, detail="运行时就绪", error="")
            else:
                result = segment_mod.run(Path(options["folder"]), cfg, options.get("audio"), progress)
                pending = result.get("needs_review_units") or []
                passed = result.get("ok") is True and not pending
                boot.write_status(data_dir, batch=batch, job=kind, running=False, ok=passed,
                                  phase="done" if passed else "needs_review", pct=100,
                                  detail=("听力切段已完成" if passed else
                                          "候选切片已保存，边界待核对：" + "、".join(pending[:12])), error="")
        except Exception as exc:  # noqa: BLE001 - 后台任务须把失败写进状态文件
            boot.write_status(data_dir, batch=batch, job=kind, running=False, ok=False,
                              phase="error", detail="", error=str(exc))
        finally:
            watchdog_stop.set()
            with srv._audio_job["lock"]:
                srv._audio_job.update({"running": False, "kind": "", "batch": ""})

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True, "kind": kind, "batch": batch}
