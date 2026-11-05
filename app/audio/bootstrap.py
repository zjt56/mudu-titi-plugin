# -*- coding: utf-8 -*-
"""懒引导：按需创建 venv、安装 WhisperX、预下载模型，全程写进度文件。

不点击「安装」就一个字节都不下载；重复执行是幂等的（有 READY 标记就跳过）。
允许通过 config.venv 复用已有虚拟环境（例如 video-kit 的 venv）。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config as cfgmod

# 固定版本，避免上游悄悄把依赖栈换成更大的组合。
WHISPERX_SPEC = "whisperx==3.8.6"
STATUS_NAME = "status.json"
READY_NAME = "READY.json"


def status_path(data_dir: Path) -> Path:
    return Path(data_dir) / STATUS_NAME


def read_status(data_dir: Path) -> dict:
    try:
        return json.loads(status_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"job": "", "running": False, "ok": None, "phase": "idle", "pct": 0, "detail": "", "error": ""}


def write_status(data_dir: Path, **fields) -> dict:
    path = status_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = read_status(data_dir)
    state.update(fields)
    state["updated"] = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return state


def venv_python(cfg: dict | None = None) -> Path:
    cfg = cfg or {}
    external = str(cfg.get("venv") or os.environ.get("WF_AUDIO_VENV") or "").strip()
    root = Path(external).expanduser() if external else Path(cfgmod.data_dir(cfg)) / "venv"
    name = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    # 不要 resolve()：venv 的 bin/python 通常是指向基础解释器的符号链接，
    # 解析成真实路径会脱离 venv（丢失 site-packages，import whisperx 失败）。
    # 保留 venv 内的路径，让解释器按 pyvenv.cfg 使用虚拟环境。
    return root / name


def venv_dir(cfg: dict | None = None) -> Path:
    return venv_python(cfg).parent.parent


def worker_path() -> Path:
    return Path(__file__).resolve().parent / "worker.py"


VENDOR_DIR = Path(__file__).resolve().parent / "vendor"


def platform_key() -> str:
    """darwin-arm64 / darwin-x64 / win32-x64 / linux-x64"""
    sysname = sys.platform
    machine = (platform.machine() or "").lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if sysname == "darwin":
        return "darwin-" + arch
    if sysname.startswith("win"):
        return "win32-" + arch
    return "linux-" + arch


def vendor_python(cfg: dict | None = None) -> Path | None:
    """随插件分发的便携 Python（优先用于建 venv）。"""
    binname = "python.exe" if os.name == "nt" else "bin/python3"
    cand = VENDOR_DIR / "python" / platform_key() / binname
    if cand.is_file():
        return cand.resolve()
    return None


def tool_path(name: str) -> str | None:
    """ffmpeg/ffprobe 解析：vendor 优先，其次系统 PATH。"""
    exe = name + (".exe" if os.name == "nt" else "")
    cand = VENDOR_DIR / "ffmpeg" / platform_key() / "bin" / exe
    if cand.is_file():
        return str(cand.resolve())
    return shutil.which(name)


def runtime_env(cfg: dict | None = None) -> dict:
    """子进程环境：模型缓存/镜像 + 把 vendor ffmpeg 目录前置到 PATH。

    whisperx 的 load_audio 内部会调 ffmpeg 子进程；不前置 PATH 时找不到 vendored 的 ffmpeg。
    """
    env = cfgmod.model_env(cfg)
    bindir = VENDOR_DIR / "ffmpeg" / platform_key() / "bin"
    if bindir.is_dir():
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
    # WhisperX 断句依赖 NLTK 的 punkt_tab；预置在 venv 内，显式指给子进程，
    # 避免运行时再去联网下载（受限网络会失败）。
    nltk_dir = venv_dir(cfg) / "nltk_data"
    if nltk_dir.is_dir():
        env["NLTK_DATA"] = str(nltk_dir)
    return env


def base_python(cfg: dict | None = None) -> str:
    """建 venv 用的解释器：vendor 便携 Python 优先，否则当前解释器。"""
    vp = vendor_python(cfg)
    return str(vp) if vp else sys.executable


def preflight() -> None:
    """安装前检查：Python 版本与 ffmpeg/ffprobe 是否可得。失败抛 RuntimeError。"""
    # whisperx 3.8.6 要求 Python >=3.10,<3.14；插件主流程只要求 >=3.9。
    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        raise RuntimeError(
            f"WhisperX 需要 Python 3.10–3.13，当前是 {sys.version.split()[0]}；"
            "请设置环境变量 WF_PYTHON 指向 3.10–3.13 的解释器后重启插件")
    for tool in ("ffmpeg", "ffprobe"):
        if not tool_path(tool):
            raise RuntimeError(
                f"缺少 {tool}：插件 vendor/ 里没有本平台（{platform_key()}）的二进制，"
                "系统 PATH 也找不到。请在有网机器运行 "
                "`python app/audio/vendor/fetch_vendor.py` 把二进制放进 vendor，"
                "或安装 ffmpeg 后重试")


def models_dir(cfg: dict | None = None) -> Path:
    return Path(cfgmod.data_dir(cfg)) / "models"


def ready(cfg: dict | None = None) -> bool:
    cfg = cfg or cfgmod.load()
    py = venv_python(cfg)
    marker = Path(cfgmod.data_dir(cfg)) / READY_NAME
    return py.is_file() and marker.is_file()


def _run(cmd: list[str], data_dir: Path, phase: str, pct: int, env: dict | None = None):
    """同步跑一条命令，实时把末行输出写进进度文件，失败抛 RuntimeError。"""
    write_status(data_dir, job="bootstrap", running=True, ok=None, phase=phase, pct=pct, detail="", error="")
    merged = dict(os.environ)
    if env:
        merged.update(env)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=merged)
    tail = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        tail = line.strip()[-300:]
        if tail:
            write_status(data_dir, detail=tail)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"{phase} 失败（退出码 {rc}）：{tail}")


def bootstrap(cfg: dict | None = None, force: bool = False) -> dict:
    """安装运行时与模型；返回最终状态。由后台线程调用，不阻塞 HTTP 请求。"""
    cfg = cfg or cfgmod.load()
    data_dir = Path(cfgmod.data_dir(cfg))
    data_dir.mkdir(parents=True, exist_ok=True)
    py = venv_python(cfg)
    marker = data_dir / READY_NAME
    if not force and marker.is_file() and py.is_file():
        return write_status(data_dir, job="bootstrap", running=False, ok=True, phase="ready", pct=100,
                            detail="已安装，无需重复下载", error="")

    try:
        preflight()
        if not py.is_file():
            if cfg.get("venv"):
                raise RuntimeError("配置的 venv 不存在可用的 Python：" + str(py))
            write_status(data_dir, job="bootstrap", running=True, ok=None, phase="venv", pct=5,
                         detail="创建虚拟环境…", error="")
            _run([base_python(cfg), "-m", "venv", str(venv_dir(cfg))], data_dir, "venv", 5)

        # Linux 上 PyPI 的 torch 默认连带数 GB 的 nvidia-* CUDA 包，即使只用 CPU。
        # 先预装 CPU 版 torch，resolver 就不再拉 CUDA 依赖；装不上则回退普通安装。
        if cfg.get("cpu_torch") and sys.platform.startswith("linux"):
            try:
                _run([str(py), "-m", "pip", "install", "--disable-pip-version-check",
                      "--index-url", cfgmod.CPU_TORCH_INDEX,
                      "torch~=2.8.0", "torchaudio~=2.8.0", "torchvision~=0.23.0"],
                     data_dir, "torch-cpu", 12,
                     env={"PIP_DISABLE_PIP_VERSION_CHECK": "1"})
            except RuntimeError:
                write_status(data_dir, detail="CPU 版 torch 不可用，回退 PyPI 默认安装（可能较大）")

        _run([str(py), "-m", "pip", "install", "--disable-pip-version-check", WHISPERX_SPEC],
             data_dir, "pip", 15,
             env={"PIP_DISABLE_PIP_VERSION_CHECK": "1"})

        models_dir(cfg).mkdir(parents=True, exist_ok=True)
        _run([str(py), str(worker_path()), "bootstrap",
              "--asr", str(cfg["asr_model"]),
              "--align", str(cfg["align_model"]),
              "--device", str(cfg["device"]),
              "--compute-type", str(cfg["compute_type"]),
              "--vad-method", str(cfg["vad_method"]),
              "--model-dir", str(models_dir(cfg))],
             data_dir, "models", 60,
             # 与 segment.transcribe() 共用同一套缓存/镜像环境，否则切段时找不到模型。
              env=cfgmod.model_env(cfg))

        marker.write_text(json.dumps({"whisperx": WHISPERX_SPEC, "asr_model": cfg["asr_model"],
                                     "align_model": cfg["align_model"], "installed_at": time.time()},
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return write_status(data_dir, job="bootstrap", running=False, ok=True, phase="ready", pct=100,
                            detail="运行时与模型已就绪", error="")
    except Exception as exc:  # noqa: BLE001 - 进度文件需要如实记录失败
        return write_status(data_dir, job="bootstrap", running=False, ok=False, phase="error",
                            detail="", error=str(exc))
