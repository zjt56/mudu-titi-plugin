# -*- coding: utf-8 -*-
"""英语听力切段：silencedetect 骨架 + WhisperX 词级题号锚点 → 真实切片 + 证据。

流程（对应 skills-reference/lessons-learned.md 第 4 条）：
  1. ffprobe 取总时长；
  2. ffmpeg silencedetect 找所有静音，≥skeleton_silence 的静音中点作为切点骨架；
  3. 在懒引导 venv 里跑 WhisperX，得到词级时间戳，只用来找 Question N / 第 N 题锚点；
  4. 把锚点吸附到「锚点之前最近的骨架切点」，相邻锚点之间即一段；
  5. ffmpeg 按真实偏移切片，写 02_audio/segment-evidence.json（verified=true）、
     audio_map.json、full-transcript.json。
绝不把转写时间戳直接当切点；无锚点的单元按骨架等分并标注 interpolated。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import bootstrap, config as cfgmod

AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")
EVIDENCE_NAME = "segment-evidence.json"
MAP_NAME = "audio_map.json"
TRANSCRIPT_NAME = "full-transcript.json"

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
ANCHOR_RE = re.compile(
    r"\b(?:question|number|part|section|item|q)\s*(?:no\.?\s*)?(\d{1,2}|[a-z]+)\b|第\s*(\d{1,2})\s*题",
    re.I,
)


def _run(cmd: list[str], *, capture=True, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(cmd, check=True, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, env=merged)


def probe_duration(audio: Path) -> float:
    out = _run([bootstrap.tool_path("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(audio)]).stdout.strip()
    try:
        value = float(out.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("无法读取音频时长：" + out[:120]) from exc
    if value <= 0:
        raise RuntimeError("音频时长必须为正数")
    return value


def detect_silences(audio: Path, noise: str, min_duration: float) -> list[tuple[float, float]]:
    """解析 ffmpeg silencedetect 输出，返回 (start,end) 列表。"""
    proc = subprocess.run(
        [bootstrap.tool_path("ffmpeg") or "ffmpeg", "-hide_banner", "-nostats", "-i", str(audio),
         "-af", f"silencedetect=noise={noise}:d={min_duration}", "-f", "null", "-"],
        text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", text)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", text)]
    pairs = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else start
        if end > start:
            pairs.append((start, end))
    return pairs


def skeleton(silences: list[tuple[float, float]], long_silence: float, duration: float) -> list[float]:
    """长静音中点作为骨架切点，含首尾。"""
    cuts = sorted({round((s + e) / 2, 3) for s, e in silences if e - s >= long_silence})
    cuts = [c for c in cuts if 0 < c < duration]
    return [0.0, *cuts, round(duration, 3)]


def load_listening_units(batch: Path) -> list[dict]:
    """从 structure.confirmed.json + types.json 取听力单元（unit_id / numbers）。"""
    structure = batch / "01_source" / "structure.confirmed.json"
    types_file = batch / "03_types" / "types.json"
    if not structure.is_file():
        raise RuntimeError("缺少 01_source/structure.confirmed.json，请先跑结构确认")
    # 结构确认里的单元可能只有 numbers/unit_kind，没有预先写死的 unit_id；
    # 统一用 agent_tasks.confirmed_units 派生稳定 ID（q-N / group-N-...），
    # 与 03_types/types.json 的 unit_id 保持一致。
    try:
        from workflow import agent_tasks
        units = agent_tasks.confirmed_units(Path(batch))
    except Exception:
        units = json.loads(structure.read_text(encoding="utf-8")).get("units") or []
    by_uid = {}
    if types_file.is_file():
        for row in (json.loads(types_file.read_text(encoding="utf-8")).get("items") or []):
            if isinstance(row, dict) and row.get("unit_id"):
                by_uid[row["unit_id"]] = str(row.get("type") or "")
    out = []
    for row in units:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("unit_id") or row.get("id") or "")
        numbers = [int(n) for n in (row.get("numbers") or []) if str(n).isdigit()]
        if not uid or not numbers:
            continue
        qtype = by_uid.get(uid, str(row.get("type") or ""))
        if qtype.startswith("听力"):
            out.append({"unit_id": uid, "numbers": numbers, "type": qtype})
    return sorted(out, key=lambda u: u["numbers"][0])


def _to_int(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def word_anchors(words: list[dict]) -> dict[int, list[float]]:
    """从词级时间戳里找 Question N / 第 N 题；只返回锚点时间，不返回切点。"""
    anchors: dict[int, list[float]] = {}
    tokens = [(t, float(w["start"])) for w in words
              if isinstance(w, dict) and w.get("start") is not None
              for t in [str(w.get("word") or "").strip()] if t]
    text = " ".join(t for t, _ in tokens)
    # 逐 token 匹配，避免跨词拼接产生假题号。
    for match in ANCHOR_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        number = _to_int(raw)
        if not number or number > 99:
            continue
        # 该匹配在拼接文本里的字符偏移 → 找到对应 token 的时间
        prefix = text[:match.start()]
        index = prefix.count(" ") if prefix else 0
        if index < len(tokens):
            anchors.setdefault(number, []).append(tokens[index][1])
    return anchors


def plan(units: list[dict], anchors: dict[int, list[float]], cuts: list[float],
         duration: float) -> list[dict]:
    """锚点吸附到之前最近的骨架切点；相邻单元首尾相接。"""
    if not units:
        return []
    raw_times: list[float | None] = []
    for u in units:
        found = [t for n in u["numbers"] for t in anchors.get(n, [])]
        raw_times.append(min(found) if found else None)
    times: list[float] = list(raw_times)  # type: ignore[arg-type]
    # 无锚点的单元：在已知锚点之间线性插值，再退化为骨架等分。
    known = [(i, t) for i, t in enumerate(raw_times) if t is not None]
    if not known:
        step = duration / (len(units) + 1)
        times = [step * (i + 1) for i in range(len(units))]
    else:
        for i, t in enumerate(raw_times):
            if t is not None:
                continue
            before = [k for k in known if k[0] < i]
            after = [k for k in known if k[0] > i]
            if before and after:
                (bi, bt), (ai, at) = before[-1], after[0]
                times[i] = bt + (at - bt) * (i - bi) / (ai - bi)
            elif before:
                times[i] = min(duration, before[-1][1] + 5.0)
            else:
                times[i] = max(0.0, after[0][1] - 5.0)
    starts = []
    for t in times:
        prior = [c for c in cuts[:-1] if c <= float(t)]
        starts.append(prior[-1] if prior else 0.0)
    # 保证严格递增：撞车就顺延到下一个骨架切点。
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            nxt = next((c for c in cuts if c > starts[i - 1] and c < duration), None)
            starts[i] = nxt if nxt is not None else min(duration - 0.5, starts[i - 1] + 0.5)
    out = []
    for i, u in enumerate(units):
        start = round(float(starts[i]), 3)
        end = round(float(starts[i + 1]) if i + 1 < len(starts) else duration, 3)
        if end <= start:
            end = min(duration, start + 0.5)
        out.append({**u, "start": start, "end": end,
                    "anchor": round(float(times[i] or 0), 3),
                    "confidence": "anchored" if raw_times[i] is not None else "interpolated"})
    return out


def find_audio(batch: Path, explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file():
            raise RuntimeError("音频文件不存在：" + str(p))
        return p
    manifest = batch / "workflow" / "manifest.json"
    if manifest.is_file():
        try:
            recorded = (json.loads(manifest.read_text(encoding="utf-8"))
                        .get("inputs", {}).get("audio", {}).get("path"))
            if recorded and Path(recorded).expanduser().is_file():
                return Path(recorded).expanduser().resolve()
        except (ValueError, OSError):
            pass
    for p in sorted(batch.rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES and "02_audio" not in p.parts:
            return p.resolve()
    raise RuntimeError("找不到整卷听力音频，请通过 --audio 指定")


def transcribe(audio: Path, cfg: dict, out: Path) -> dict:
    py = bootstrap.venv_python(cfg)
    if not py.is_file():
        raise RuntimeError("尚未安装 WhisperX 运行时，请先在设置页点击「安装」")
    _run([str(py), str(bootstrap.worker_path()), "transcribe",
          "--audio", str(audio), "--out", str(out),
          "--asr", str(cfg["asr_model"]), "--align", str(cfg["align_model"]),
          "--device", str(cfg["device"]), "--compute-type", str(cfg["compute_type"]),
          "--vad-method", str(cfg["vad_method"]),
          "--model-dir", str(bootstrap.models_dir(cfg))],
         # 必须和 bootstrap 用同一套模型缓存/镜像，否则会重新下载或找不到权重。
         env=bootstrap.runtime_env(cfg))
    return json.loads(out.read_text(encoding="utf-8"))


def cut(audio: Path, out_dir: Path, item: dict) -> list[str]:
    """按真实偏移切一段；返回可写进 evidence 的真实命令记录。"""
    name = "seg-" + re.sub(r"[^A-Za-z0-9._-]", "-", str(item["unit_id"])) + ".mp3"
    target = out_dir / name
    length = round(item["end"] - item["start"], 3)
    base = [bootstrap.tool_path("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(item["start"]), "-i", str(audio), "-t", str(length)]
    commands = [base + ["-c:a", "libmp3lame", "-q:a", "4", str(target)],
                base + ["-c:a", "copy", str(target)]]
    last = ""
    for cmd in commands:
        proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            return ["ffmpeg " + " ".join(cmd[5:]),
                    f"silencedetect 骨架 + WhisperX 词级锚点 {item['anchor']}s（{item['confidence']}）",
                    f"整卷偏移 {item['start']}s → {item['end']}s，切片 {name} 已生成"]
        last = (proc.stderr or "").strip()[-200:]
    raise RuntimeError(f"切段失败 {item['unit_id']}：{last}")


def run(batch: Path, cfg: dict | None = None, audio: str | None = None,
        progress=None) -> dict:
    """执行完整切段；返回摘要。progress(phase, pct, detail) 可选。"""
    cfg = cfg or cfgmod.load()
    batch = Path(batch).resolve()
    out_dir = batch / "02_audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda *_: None)

    src = find_audio(batch, audio)
    report("probe", 5, "读取音频时长")
    duration = probe_duration(src)
    report("silence", 15, "silencedetect 定骨架")
    silences = detect_silences(src, cfg["noise"], cfg["cut_silence"])
    cuts = skeleton(silences, float(cfg["skeleton_silence"]), duration)
    report("units", 25, "读取听力单元")
    units = load_listening_units(batch)
    if not units:
        raise RuntimeError("该批次没有听力单元，无需切段")

    transcript_path = out_dir / TRANSCRIPT_NAME
    report("transcribe", 35, "WhisperX 转写与词级对齐")
    transcript = transcribe(src, cfg, transcript_path)
    anchors = word_anchors(transcript.get("words") or [])
    report("plan", 75, f"骨架 {len(cuts) - 2} 个切点，锚点 {sum(len(v) for v in anchors.values())} 个")
    plan_rows = plan(units, anchors, cuts, duration)

    items = []
    for i, row in enumerate(plan_rows):
        evidence = cut(src, out_dir, row)
        items.append({"unit_id": row["unit_id"],
                      "file": "02_audio/" + "seg-" + re.sub(r"[^A-Za-z0-9._-]", "-", str(row["unit_id"])) + ".mp3",
                      "start": row["start"], "end": row["end"],
                      "verified": True, "evidence": evidence})
        report("cut", 75 + int(20 * (i + 1) / len(plan_rows)), row["unit_id"])

    evidence_doc = {"generator": "audio.segment/v1", "audio": str(src),
                    "duration": duration, "config": cfg,
                    "silence_skeleton": cuts, "anchors": {str(k): v for k, v in anchors.items()},
                    "items": items}
    (out_dir / EVIDENCE_NAME).write_text(json.dumps(evidence_doc, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    (out_dir / MAP_NAME).write_text(json.dumps({"items": [
        {"unit_id": it["unit_id"], "file": it["file"], "start": it["start"], "end": it["end"]}
        for it in items]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report("done", 100, f"已生成 {len(items)} 段")
    return {"ok": True, "units": len(items), "duration": duration,
            "skeleton_cuts": len(cuts) - 2, "anchors": sum(len(v) for v in anchors.values()),
            "evidence": str(out_dir / EVIDENCE_NAME)}
