# -*- coding: utf-8 -*-
"""听力切段配置：默认值、数据根、原子读写。

配置落在插件 config/audio.json（与 model-router.json 同级），随插件分发；
运行时/模型放数据根 audio/ 子目录，升级重装插件不丢。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
PLUGIN_ROOT = APP_ROOT.parent

DEFAULTS = {
    # 默认不启用：不点击「安装」就不会下载任何依赖/模型。
    "enabled": False,
    # 默认多语种 small：资源占用更低；用户可在设置页显式切换 large-v3-turbo。
    "asr_model": "small",
    # CPU 内存/线程预算：默认一次只运行一份模型。
    "cpu_threads": 4,
    "batch_size": 2,
    "max_parallel_transcriptions": 1,
    "compute_type": "int8",
    "device": "cpu",
    # 对齐模型走 HuggingFace repo（而不是 torchaudio bundle）：torchaudio 的权重在
    # download.pytorch.org，国内常不可达；HF 可用镜像。约 360MB。
    "align_model": "facebook/wav2vec2-base-960h",
    # pyannote VAD 用 whisperx wheel 自带的 assets/pytorch_model.bin，完全不联网、
    # 不需要 HF token；silero 反而要从 GitHub 拉 torch.hub 模型。
    "vad_method": "pyannote",
    # 题号播报语言：中文卷的听力音频用中文念「四、短文理解」「听下面一段对话，
    # 回答第16至17小题」。填 zh 时额外跑一遍中文转写，只取题号/大题锚点
    # （不做词级对齐），保留真实段级时间；并行数受 max_parallel_transcriptions 限制。
    # 空 = 不跑第二遍（纯英语音频可关掉省时间）。
    "anchor_language": "zh",
    # 骨架：≥3s 长静音定切点；cut_silence 用于探测更细的候选静音。
    "skeleton_silence": 3.0,
    "cut_silence": 0.35,
    "noise": "-35dB",
    # 空 = 数据根自建 venv；也可填已有的 venv 目录复用（仍会补齐缺失依赖）。
    "venv": "",
    # 空 = WF_AUDIO_DATA_DIR / WF_DATA_DIR / ~/.dsh/wf-plugin-data 下的 audio/。
    "data_dir": "",
    # 模型下载镜像。空 = 自动：huggingface.co 可达就直连，否则用 hf-mirror.com。
    # 也可显式填任意 HF_ENDPOINT（如 https://hf-mirror.com、公司内网镜像）。
    "hf_endpoint": "",
    # Linux 上 PyPI 默认 torch 会连带数 GB 的 nvidia-* CUDA 包；
    # true 时先从 download.pytorch.org/whl/cpu 预装 CPU 版 torch（失败则回退 PyPI）。
    "cpu_torch": True,
}

ALLOWED = {
    "asr_model": ("large-v3-turbo", "large-v3", "large-v2", "large-v1", "large", "medium",
                  "tiny.en", "base.en", "small.en", "medium.en", "small", "base", "tiny"),
    "compute_type": ("int8", "int8_float16", "float16", "float32"),
    "device": ("cpu", "cuda"),
    "vad_method": ("pyannote", "silero"),
    # 允许 HF repo id 或 torchaudio bundle 名。
    "align_model": ("facebook/wav2vec2-base-960h", "WAV2VEC2_ASR_BASE_960H",
                    "facebook/wav2vec2-large-960h-lv60-self"),
    "anchor_language": ("", "zh", "en", "auto"),
}


def config_path() -> Path:
    """随插件分发的默认配置（只读）。用户改动写数据根，不写包目录。"""
    env = os.environ.get("WF_AUDIO_CONFIG")
    return Path(env).expanduser() if env else PLUGIN_ROOT / "config" / "audio.json"


def user_config_path() -> Path:
    env = os.environ.get("WF_AUDIO_CONFIG")
    if env:
        return Path(env).expanduser()
    # 只看环境变量：避免 data_dir(cfg) 与配置自身互相依赖。
    return data_dir({}) / "config.json"


def _base_dir() -> Path:
    """与 lib/index.js 的 resolveDataRoot() 保持完全一致的优先级。

    WF_DATA_DIR > 插件目录内已有 workflows/（旧数据兼容） > DSH_HOME/wf-plugin-data
    > ~/.dsh/wf-plugin-data。宿主新旧版本都必须解析到同一处，否则重启后
    已装好的 venv/模型会「凭空消失」。
    """
    if os.environ.get("WF_DATA_DIR"):
        return Path(os.environ["WF_DATA_DIR"]).expanduser()
    if (PLUGIN_ROOT / "workflows").is_dir():
        return PLUGIN_ROOT
    if os.environ.get("DSH_HOME"):
        return Path(os.environ["DSH_HOME"]).expanduser() / "wf-plugin-data"
    return Path.home() / ".dsh" / "wf-plugin-data"


def data_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or {}
    explicit = cfg.get("data_dir") or os.environ.get("WF_AUDIO_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (_base_dir() / "audio").resolve()


def _read(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load() -> dict:
    cfg = dict(DEFAULTS)
    for source in (config_path(), user_config_path()):
        cfg.update({k: v for k, v in _read(source).items() if k in DEFAULTS})
    return cfg


def validate(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("配置必须是对象")
    out = dict(DEFAULTS)
    for key, value in cfg.items():
        if key not in DEFAULTS:
            raise ValueError("未知配置项：" + key)
        if key in ALLOWED and value not in ALLOWED[key]:
            raise ValueError(f"{key} 只能是 {', '.join(ALLOWED[key])}")
        if key in ("cpu_threads", "batch_size", "max_parallel_transcriptions"):
            upper = {"cpu_threads": 64, "batch_size": 64, "max_parallel_transcriptions": 2}[key]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f"{key} 必须是 1~{upper} 的整数")
        if key in ("skeleton_silence", "cut_silence"):
            value = float(value)
            if not 0 < value <= 30:
                raise ValueError(f"{key} 必须在 0~30 秒之间")
        if key == "noise":
            if not isinstance(value, str) or not value.endswith("dB"):
                raise ValueError("noise 需形如 -35dB")
        if key in ("venv", "data_dir", "hf_endpoint"):
            value = str(value or "").strip()
            if key == "hf_endpoint" and value and not value.startswith(("http://", "https://")):
                raise ValueError("hf_endpoint 需以 http(s):// 开头")
        if key in ("enabled", "cpu_torch"):
            value = bool(value)
        out[key] = value
    if out["cut_silence"] > out["skeleton_silence"]:
        raise ValueError("cut_silence 不能大于 skeleton_silence")
    return out


def save(cfg: dict) -> dict:
    out = validate(cfg)
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return out


HF_MIRROR = "https://hf-mirror.com"
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


@lru_cache(maxsize=1)
def _hf_direct_reachable() -> bool:
    """进程内只探测一次，避免设置页每次刷新都等网络超时。"""
    try:
        import urllib.request
        urllib.request.urlopen("https://huggingface.co/api/models?limit=1", timeout=2)
        return True
    except Exception:  # noqa: BLE001 - 网络不可达即走镜像
        return False


def resolve_hf_endpoint(cfg: dict | None = None) -> str:
    """返回 HF_ENDPOINT：显式配置 > 环境变量 > 自动探测（直连不通就用镜像）。"""
    cfg = cfg or {}
    explicit = str(cfg.get("hf_endpoint") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    from_env = (os.environ.get("HF_ENDPOINT") or "").strip()
    if from_env:
        return from_env.rstrip("/")
    return "" if _hf_direct_reachable() else HF_MIRROR


def model_env(cfg: dict | None = None) -> dict:
    """bootstrap 与切段共用的模型缓存环境。

    两边必须完全一致：否则安装时下到数据根、切段时却去 ~/.cache/huggingface 找，
    会重新下载或找不到模型（已修的真实缺陷）。
    """
    cfg = cfg or {}
    root = data_dir(cfg) / "models"
    env = {
        "HF_HOME": str(root),
        "HUGGINGFACE_HUB_CACHE": str(root / "hub"),
        "TRANSFORMERS_CACHE": str(root / "transformers"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        # 让 bootstrap/segment 启动的 worker 继承一致资源预算。
        "WF_AUDIO_CPU_THREADS": str(cfg.get("cpu_threads", DEFAULTS["cpu_threads"])),
        "WF_AUDIO_BATCH_SIZE": str(cfg.get("batch_size", DEFAULTS["batch_size"])),
        "WF_AUDIO_MAX_PARALLEL": str(cfg.get("max_parallel_transcriptions", DEFAULTS["max_parallel_transcriptions"])),
        "OMP_NUM_THREADS": str(cfg.get("cpu_threads", DEFAULTS["cpu_threads"])),
        "MKL_NUM_THREADS": str(cfg.get("cpu_threads", DEFAULTS["cpu_threads"])),
        "OPENBLAS_NUM_THREADS": str(cfg.get("cpu_threads", DEFAULTS["cpu_threads"])),
        "TOKENIZERS_PARALLELISM": "false",
    }
    endpoint = resolve_hf_endpoint(cfg)
    if endpoint:
        env["HF_ENDPOINT"] = endpoint
    return env
