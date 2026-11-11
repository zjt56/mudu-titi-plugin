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

import bisect
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

from . import bootstrap, config as cfgmod
from .binding import listening_context, assert_current

AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")
EVIDENCE_NAME = "segment-evidence.json"
MAP_NAME = "audio_map.json"
TRANSCRIPT_NAME = "full-transcript.json"
ANNOUNCE_NAME = "announce-transcript.json"
# 一个听力单元至少应有这么多秒；短于此视为「被挤进同一骨架切点之间」的微段，
# 不信任其边界，不标 verified，交人工回听。
MIN_SEGMENT_SECONDS = 2.0
# Do not assume a fixed minimum duration for every paper/item.
# Monotonic anchors and real silence boundaries provide the constraints.
MIN_ANCHOR_GAP = 0.0

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
# 题号数值：阿拉伯数字（1-2 位）或英文数词（one..twenty）。
_NUM = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
# 中文题号：阿拉伯数字或中文数字（十六/二十），题号与「题」之间可能被分词插入空格。
_CN_NUM = r"\d{1,2}|[一二两三四五六七八九十]{1,3}"
# 题号触发词（题号线索）：question/questions/q/number/item。
# 刻意排除 part/section/text/conversation —— 它们是章节词而不是题号，会把 "Section B"
# 里的 B 误当成题号。\b 与复数兼容：question(s)? 完整匹配，"questions" 不会再被当成
# "question" + 字母 "s"。
_CUE = r"(?:questions?|q|numbers?|items?)"
ANCHOR_RE = re.compile(
    # 1) 触发词 + 可选 no. + 一个题号，后接可能的范围 "A and B" / "A-B" / "A to B"
    rf"\b{_CUE}\s*(?:no\.?\s*)?{_NUM}"
    rf"(?:\s*(?:and|to|-|–|—)\s*{_NUM})?"
    # 2) 中文「第 N 题」，可选范围「第 6、7 题」/「第 6 至 7 题」；
    #    卷面与播报常写成「第16至17小题」，所以题号后允许「小」。
    r"|第\s*(" + _CN_NUM + r")\s*(?:[、,，]|至|到|-|–)\s*(" + _CN_NUM + r")?\s*小?\s*[题題]"
    r"|第\s*(" + _CN_NUM + r")\s*小?\s*[题題]",
    re.I,
)


def _run(cmd: list[str], *, capture=True, env: dict | None = None,
         on_progress=None) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    if on_progress is None or not capture:
        return subprocess.run(cmd, check=True, text=True, encoding="utf-8", errors="replace",
                              stdout=subprocess.PIPE if capture else None,
                              stderr=subprocess.PIPE if capture else None, env=merged, timeout=1800)
    import queue
    import threading
    process = subprocess.Popen(cmd, text=True, encoding="utf-8", errors="replace",
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=merged)
    lines = queue.Queue()
    def reader():
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)
    thread = threading.Thread(target=reader, daemon=True); thread.start()
    started = time.monotonic(); tail = []; finished = False
    try:
        while not finished:
            if time.monotonic() - started > 1800:
                raise subprocess.TimeoutExpired(cmd, 1800)
            try:
                line = lines.get(timeout=.25)
            except queue.Empty:
                continue
            if line is None:
                finished = True
                continue
            tail.append(line)
            if len(tail) > 2000:
                del tail[:500]
            try:
                value = json.loads(line)
                if isinstance(value, dict) and isinstance(value.get("detail"), str):
                    on_progress(value)
            except (ValueError, TypeError):
                pass
        code = process.wait(timeout=10)
        output = "".join(tail)
        if code:
            raise subprocess.CalledProcessError(code, cmd, output=output, stderr=output)
        return subprocess.CompletedProcess(cmd, code, stdout=output, stderr="")
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
        thread.join(timeout=1)


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
        text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError("静音检测失败：" + (proc.stderr or "")[-600:])
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    starts = [float(m) for m in re.findall(r"silence_start:\s*(" + number + ")", text)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*(" + number + ")", text)]
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
    """Read only the catalog-valid, exactly covered confirmed/type version."""
    from .binding import listening_context
    return listening_context(batch)[1]


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(token: str) -> int | None:
    """解析「十六」「二十」「十八」这类中文数字（1–99）。"""
    text = (token or "").strip()
    if not text:
        return None
    if "十" not in text:
        return _CN_DIGITS.get(text) if len(text) == 1 else None
    head, _, tail = text.partition("十")
    tens = 1 if not head else _CN_DIGITS.get(head)
    ones = 0 if not tail else _CN_DIGITS.get(tail)
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


def _to_int(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token) or _cn_number(token)


def _span(start: int, end: int) -> list[int]:
    """把 [start, end] 展开成题号列表；不合法或过大范围返回空。"""
    if start is None or end is None or end < start:
        return []
    if end - start > 30:  # 防御：绝不因一句“第1到99题”制造海量锚点
        return [start]
    return list(range(start, end + 1))


def word_anchors(words: list[dict]) -> dict[int, list[float]]:
    """从词级时间戳里找题号锚点（Question N / 第 N 题 / 裸题号，支持范围）。

    只返回锚点时间，不返回切点。同一句里宣告的范围（如 “questions 16 to 20”）
    各题号共享该句的起始时间，作为整组起点锚。

    很多听力音频不用 “Question 3” 而直接念 “3.” 或 “Three,”（中文卷尤其如此），
    所以除线索词外还识别句首裸题号。识别出的候选再按“题号与时间必须同向递增”
    取最长链，丢掉句中数字（如 “a number two bus”）造成的假锚点。
    """
    tokens: list[tuple[str, float, float | None]] = []
    prev_end = None
    for word in words:
        if not isinstance(word, dict) or word.get("start") is None:
            continue
        text_token = str(word.get("word") or "").strip()
        if not text_token:
            continue
        try:
            start = float(word["start"])
        except (TypeError, ValueError):
            continue
        try:
            end = float(word["end"]) if word.get("end") is not None else start
        except (TypeError, ValueError):
            end = start
        gap = None if prev_end is None else max(0.0, start - prev_end)
        tokens.append((text_token, start, gap))
        prev_end = end
    if not tokens:
        return {}
    text = " ".join(token for token, _, _ in tokens)
    offsets, cursor = [], 0
    for token, _, _ in tokens:
        offsets.append(cursor)
        cursor += len(token) + 1

    def at(match_start: int) -> float | None:
        # Tokens may themselves contain spaces (real ASR segments); counting spaces
        # maps these matches onto unrelated later timestamps.
        index = bisect.bisect_right(offsets, match_start) - 1
        return tokens[index][1] if 0 <= index < len(tokens) else None

    candidates: list[tuple[int, float]] = []

    def add(number, when) -> None:
        if when is None or not number or number > 99:
            return
        candidates.append((int(number), round(float(when), 3)))

    # 逐 token 拼接匹配，避免跨词拼接产生假题号。
    for match in ANCHOR_RE.finditer(text):
        when = at(match.start())
        if match.group(1) and re.match(r"numbers?\b", match.group(0), re.I):
            prefix = text[:match.start()].rstrip()
            # A bus/room number inside the dialogue is not a question cue.
            if re.search(r"\b(?:a|the|my|your|his|her|our|their)\s*$", prefix, re.I):
                continue
        # 英文触发词：g1 起始题号，g2 可选范围结束
        if match.group(1):
            start = _to_int(match.group(1))
            end = _to_int(match.group(2)) if match.group(2) else None
            if end is not None and start is not None and end > start:
                for n in _span(start, end):
                    add(n, when)
            else:
                add(start, when)
            continue
        # 中文范围：g3 起始，g4 可选结束
        if match.group(3):
            start = _to_int(match.group(3))
            end = _to_int(match.group(4)) if match.group(4) else None
            if end is not None and start is not None and end > start:
                for n in _span(start, end):
                    add(n, when)
            else:
                add(start, when)
            continue
        # 中文单题：g5
        if match.group(5):
            add(_to_int(match.group(5)), when)

    # 裸题号：数字带句点（"8." "10."）或句首独立数词（"Six," "nine"）。
    for token, start, gap in tokens:
        bare = token.strip(".,!?;:，。")
        if not bare:
            continue
        at_start = gap is None or gap >= 1.2
        if re.fullmatch(r"\d{1,2}", bare):
            if token.rstrip().endswith((".", "．")) or at_start:
                add(int(bare), start)
        elif bare.lower() in NUMBER_WORDS and at_start:
            add(NUMBER_WORDS[bare.lower()], start)

    if not candidates:
        return {}
    # 题号与时间必须同向：对 (题号升序, 时间不减) 取最长链。
    candidates.sort()
    chains: list[list[tuple[int, float]]] = []
    for item in candidates:
        best: list[tuple[int, float]] = []
        for chain in chains:
            last = chain[-1]
            gap = item[1] - last[1]
            # gap==0 表示同一句里宣告的范围（questions 6 and 7），必须保留；
            # 不同句之间的题号至少要隔 MIN_ANCHOR_GAP，否则是误识别。
            if (last[0] < item[0] and (gap == 0 or gap >= MIN_ANCHOR_GAP)
                    and len(chain) > len(best)):
                best = chain
        chains.append(best + [item])
    longest = max(chains, key=len)
    anchors: dict[int, list[float]] = {}
    for number, when in longest:
        anchors.setdefault(number, [])
        if when not in anchors[number]:
            anchors[number].append(when)
    return anchors



# 中文听力大题播报的标题词 → 题型。中文卷的音频会在每个大题前念「四、短文理解
# 你将听到一篇短文…」，但短文理解/信息转换不会逐题念题号，所以大题标题是这些
# 单元唯一可靠的起点锚。
SECTION_KEYWORDS = (
    ("关键词语选择", ("关键词语", "關鍵詞語", "关键词", "關鍵詞")),
    ("短对话理解", ("短对话理解", "短對話理解", "短对话", "短對話")),
    ("长对话理解", ("长对话理解", "長對話理解", "长对话", "長對話")),
    ("短文理解", ("短文理解",)),
    ("信息转换", ("信息转换", "信息轉換", "信息转换")),
)


def section_anchors(units: list[dict], words: list[dict]) -> dict[int, float]:
    """用中文大题播报定位每个大题的起点，返回 {首个题号: 时间}。

    只在同一种大题连续出现时取第一处播报；按单元顺序向后搜索，避免把
    「短对话理解」里的“对话”误当成后面的大题。
    """
    tokens = [(str(w.get("word") or "").strip(), float(w.get("start") or 0.0))
              for w in words or [] if isinstance(w, dict) and w.get("start") is not None]
    tokens = [(t, ts) for t, ts in tokens if t]
    if not tokens:
        return {}
    text = " ".join(t for t, _ in tokens)
    offsets, position = [], 0
    for token, _ in tokens:
        offsets.append(position)
        position += len(token) + 1

    def time_at(offset: int) -> float:
        return tokens[max(0, bisect.bisect_right(offsets, offset) - 1)][1]

    found: dict[int, float] = {}
    cursor = 0
    seen_section = None
    for unit in units:
        main_type = str(unit.get("type") or "")
        keywords = next((kws for name, kws in SECTION_KEYWORDS if name in main_type), ())
        if not keywords:
            continue
        if main_type == seen_section:
            continue  # 同一大题的后续单元沿用该大题起点，不再另找播报
        seen_section = main_type
        best = None
        for keyword in keywords:
            offset = text.find(keyword, cursor)
            while offset >= 0:
                candidate = time_at(offset)
                if best is None or candidate < best[0]:
                    best = (candidate, offset + len(keyword))
                offset = text.find(keyword, offset + 1)
            if best is not None:
                break
        if best is not None:
            cursor = best[1]
            found.setdefault(int(unit["numbers"][0]), best[0])
    return found


def transcript_anchors(document: dict, units: list[dict]) -> tuple[dict[int, list[float]], set[int]]:
    """Preserve source precision and exclude spoken section numbers from question IDs."""
    segments = document.get("segments") or []
    if not segments:
        return word_anchors(document.get("words") or []), set()
    precise, rough, real_segments = {}, {}, []
    for row in segments:
        if not isinstance(row, dict) or row.get("start") is None:
            continue
        text = str(row.get("text") or "").strip()
        start, end = float(row["start"]), float(row.get("end", row["start"]))
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            continue
        real_segments.append({"word": text, "start": start, "end": end})
        title = any(keyword in text for _, keywords in SECTION_KEYWORDS for keyword in keywords)
        if title:
            continue  # "2. 短对话理解" is not question two.
        true_words = row.get("words") or []
        if row.get("timestamp_precision") == "word" and true_words:
            matched = word_anchors(true_words)
            for number, times in matched.items():
                precise.setdefault(number, []).extend(times)
            continue
        matched = word_anchors([{"word": text, "start": start, "end": end}])
        first = re.match(r"^(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")[.,，．]?\s+", text, re.I)
        if first:
            number = _to_int(first.group(1))
            if number:
                matched.setdefault(number, []).append(start)
        # ASR may print 1.The with no space.
        numeric = re.match(r"^(\d{1,2})[.,，．](?!\d)", text)
        if numeric:
            matched.setdefault(int(numeric.group(1)), []).append(start)
        for number, times in matched.items():
            rough.setdefault(number, []).extend(times)
    for number, when in section_anchors(units, real_segments).items():
        if number not in precise:
            rough.setdefault(number, []).append(when)
    coarse = set()
    for number, times in rough.items():
        if number not in precise:
            precise[number] = sorted(set(times))
            coarse.add(number)
    ordered = _merge_anchors(precise)
    return ordered, coarse.intersection(ordered)


def region_bound_anchors(document: dict, units: list[dict], cuts: list[float],
                         existing: dict[int, list[float]]) -> tuple[dict[int, list[float]], dict]:
    """A coarse cue can prove a physical region, never a fabricated word time.

    Missing standalone IDs are resolved only when a whole source-confirmed section
    has exactly one speech region per unit AND all available IDs agree in order.
    """
    regions = document.get("regions") or []
    by_id, speech = {}, {}
    for row in regions:
        if not isinstance(row, dict) or not isinstance(row.get("region_id"), int):
            return {}, {}
        start, end = row.get("start"), row.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or not 0 <= start < end:
            return {}, {}
        if not all(any(abs(point - cut) < .0011 for cut in cuts) for point in (start, end)):
            return {}, {}
        by_id[row["region_id"]] = row
    for row in document.get("segments") or []:
        region_id = row.get("region_id")
        region = by_id.get(region_id)
        if region is None or row.get("start", -1) < region["start"] - .0011 or row.get("end", math.inf) > region["end"] + .0011:
            return {}, {}
        if str(row.get("text") or "").strip():
            speech.setdefault(region_id, []).append(row)
    if not by_id or not speech:
        return {}, {}
    ordered = sorted(by_id, key=lambda key: by_id[key]["start"])
    resolved, proof = {}, {}
    sections = []
    for unit in units:
        if not sections or sections[-1][0] != unit.get("type"):
            sections.append((unit.get("type"), [unit]))
        else:
            sections[-1][1].append(unit)
    title_regions = []
    after = -1
    for qtype, members in sections:
        keywords = next((words for name, words in SECTION_KEYWORDS if name in str(qtype)), ())
        matches = [rid for rid in ordered if rid > after and rid in speech
                   and any(any(word in row["text"] for word in keywords) for row in speech[rid])]
        if not matches:
            return {}, {}  # Cannot bound an entire section conservatively.
        rid = matches[0]; title_regions.append(rid); after = rid
    for index, ((_, members), title_rid) in enumerate(zip(sections, title_regions)):
        stop = title_regions[index+1] if index+1 < len(title_regions) else max(ordered)+1
        if all(len(unit["numbers"]) == 1 for unit in members):
            # Exclude the introductory region, but retain every real speech region.
            utterances = [rid for rid in ordered if title_rid < rid < stop and rid in speech]
            if len(utterances) != len(members):
                continue
            matches, conflict = 0, False
            for unit, rid in zip(members, utterances):
                number = int(unit["numbers"][0]); region = by_id[rid]
                times = existing.get(number) or []
                if times:
                    if any(region["start"] <= t < region["end"] for t in times):
                        matches += 1
                    elif unit is members[0] and all(by_id[title_rid]["start"] <= t < by_id[title_rid]["end"] for t in times):
                        pass  # Section title cues are valid but precede its first item.
                    else:
                        conflict = True
            if conflict or not matches:
                continue
            for position, (unit, rid) in enumerate(zip(members, utterances)):
                number = int(unit["numbers"][0])
                boundary_region = title_rid if position == 0 else rid
                resolved[number] = [by_id[boundary_region]["start"]]
                proof[number] = {"method": "source_section_count_and_consistent_anchors",
                                 "region_id": boundary_region, "speech_region_id": rid,
                                 "source_unit_count": len(members),
                                 "speech_region_count": len(utterances)}
        elif len(members) == 1:
            number = int(members[0]["numbers"][0]); resolved[number] = [by_id[title_rid]["start"]]
            proof[number] = {"method": "explicit_section_title_in_physical_region", "region_id": title_rid}
        else:
            # Multiple dialogues within one section require explicit full group ranges.
            found = []
            for unit in members:
                expected = {int(n) for n in unit["numbers"]}
                matches = []
                for rid in ordered:
                    if not title_rid <= rid < stop:
                        continue
                    for row in speech.get(rid, []):
                        for match in ANCHOR_RE.finditer(row["text"]):
                            first = _to_int(match.group(1) or match.group(3) or match.group(5) or "")
                            last = _to_int(match.group(2) or match.group(4) or "")
                            if first is not None and last is not None and set(_span(first, last)) == expected:
                                matches.append(rid)
                if len(set(matches)) != 1:
                    found = []; break
                found.append((unit, matches[0]))
            if len(found) != len(members) or len({rid for _, rid in found}) != len(found):
                continue
            for unit, rid in found:
                number = int(unit["numbers"][0]); resolved[number] = [by_id[rid]["start"]]
                proof[number] = {"method": "explicit_complete_group_range_in_physical_region", "region_id": rid}
    return resolved, proof


def _has_section_coverage(document: dict, units: list[dict]) -> bool:
    first_numbers = {int(unit["numbers"][0]) for i, unit in enumerate(units)
                     if i == 0 or unit.get("type") != units[i-1].get("type")}
    available, _ = transcript_anchors(document, units)
    return bool(first_numbers) and first_numbers.issubset(available)


def _merge_anchors(*groups: dict[int, list[float]]) -> dict[int, list[float]]:
    """合并多路锚点，并强制题号与时间同向（取最长递增链）。"""
    candidates: list[tuple[int, float]] = []
    for group in groups:
        for number, times in (group or {}).items():
            for when in times or []:
                candidates.append((int(number), round(float(when), 3)))
    if not candidates:
        return {}
    candidates.sort()
    chains: list[list[tuple[int, float]]] = []
    for item in candidates:
        best: list[tuple[int, float]] = []
        for chain in chains:
            last = chain[-1]
            gap = item[1] - last[1]
            # gap==0 表示同一句里宣告的范围（questions 6 and 7），必须保留；
            # 不同句之间的题号至少要隔 MIN_ANCHOR_GAP，否则是误识别。
            if (last[0] < item[0] and (gap == 0 or gap >= MIN_ANCHOR_GAP)
                    and len(chain) > len(best)):
                best = chain
        chains.append(best + [item])
    longest = max(chains, key=len)
    merged: dict[int, list[float]] = {}
    for number, when in longest:
        merged.setdefault(number, [])
        if when not in merged[number]:
            merged[number].append(when)
    return merged


def fine_silence_cuts(silences: list[tuple[float, float]], cut_silence: float,
                      skeleton_silence: float, duration: float) -> list[float]:
    """细静音中点：时长为 [cut_silence, skeleton_silence) 的静音中点。

    这些不足以当骨架切点（太短，可能是句中停顿），但当某个题号锚点离最近的
    长静音骨架切点较远、却正好落在一处细静音旁时，可用它把边界吸附得更准。
    """
    cuts = sorted({round((s + e) / 2, 3) for s, e in silences
                   if cut_silence <= e - s < skeleton_silence})
    return [c for c in cuts if 0 < c < duration]


def _snap(anchor: float, cuts: list[float], fine_cuts: list[float],
          near_window: float = 4.0) -> float:
    """Word/segment timestamps locate anchors; only long-silence midpoints cut audio."""
    prior = [c for c in cuts[:-1] if c <= anchor]
    return prior[-1] if prior else 0.0


def plan(units: list[dict], anchors: dict[int, list[float]], cuts: list[float],
         duration: float, fine_cuts: list[float] | None = None,
         coarse_numbers: set[int] | None = None) -> list[dict]:
    """Produce candidates without presenting interpolation as verified boundaries.

    Every supported boundary is a real long-silence midpoint. Missing/colliding
    anchors remain explicit uncertainty, including the previous unit's end.
    """
    if not units:
        return []
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("音频时长必须为有限正数")
    coarse_numbers = coarse_numbers or set()
    physical = sorted({0.0, float(duration), *[float(c) for c in cuts
                        if math.isfinite(float(c)) and 0 < float(c) < duration]})
    raw_times, coarse = [], []
    for unit in units:
        available = [(float(t), n) for n in unit["numbers"] for t in anchors.get(n, [])
                     if math.isfinite(float(t)) and 0 <= float(t) < duration]
        chosen = min(available) if available else None
        raw_times.append(chosen[0] if chosen else None)
        coarse.append(chosen is not None and chosen[1] in coarse_numbers)
    starts = [None] * len(units)
    supported = [False] * len(units)
    previous = -1.0
    for index, anchor in enumerate(raw_times):
        if anchor is None:
            continue
        boundary = _snap(anchor, physical, [])
        if boundary <= previous:
            # Do not substitute a word timestamp or fabricate a 0.5 second cut.
            continue
        starts[index] = boundary
        supported[index] = not coarse[index]
        previous = boundary
    if starts[0] is None:
        starts[0] = 0.0
    fixed = [i for i, value in enumerate(starts) if value is not None]
    for left, right in zip([-1, *fixed], [*fixed, len(units)]):
        missing = list(range(left + 1, right))
        if not missing:
            continue
        lo = 0.0 if left < 0 else starts[left]
        hi = duration if right == len(units) else starts[right]
        last = lo
        for order, index in enumerate(missing):
            ideal = lo + (hi - lo) * (order + 1) / (len(missing) + 1)
            upper = hi - MIN_SEGMENT_SECONDS * (len(missing) - order)
            candidates = [c for c in physical if last + MIN_SEGMENT_SECONDS <= c <= upper]
            # Keep equal-duration synthetic candidates only for diagnostics/playback.
            # They are explicitly not verified and never installed as trusted cuts.
            nearby = [c for c in candidates if abs(c - ideal) <= (hi - lo) / (len(missing) + 1) * .5]
            starts[index] = min(nearby, key=lambda c: abs(c - ideal)) if nearby else ideal
            last = starts[index]
    rows = []
    for index, unit in enumerate(units):
        start = round(float(starts[index]), 3)
        end = round(float(starts[index + 1]) if index + 1 < len(starts) else duration, 3)
        span = round(end - start, 3)
        reasons = []
        if span < MIN_SEGMENT_SECONDS:
            reasons.append("micro_segment")
        if raw_times[index] is None:
            reasons.append("missing_question_anchor")
        if coarse[index]:
            reasons.append("segment_precision_anchor")
        if not supported[index]:
            reasons.append("unsupported_start")
        if index + 1 < len(units) and not supported[index + 1]:
            reasons.append("unsupported_end")
        on_silence = all(any(abs(v - c) < .0011 for c in physical) for v in (start, end))
        if not on_silence:
            reasons.append("not_long_silence_boundary")
        quality = "micro" if span < MIN_SEGMENT_SECONDS else (
            "ok" if not reasons else "coarse" if coarse[index] else "interpolated")
        rows.append({**unit, "start": start, "end": end, "duration": span,
                     "anchor": raw_times[index], "confidence": "anchored" if raw_times[index] is not None else "interpolated",
                     "quality": quality, "boundary_verified": not reasons,
                     "semantic_verified": False, "review_reasons": reasons})
    return rows


def assign_audio_units(units: list[dict], anchors_by_audio: dict[str, dict]) -> dict:
    """Assign a whole unit only when its numbered anchors identify one recording.

    A group cannot be split between recordings, and a repeated anchor does not
    establish which recording belongs to that unit. Preserve ambiguities so the
    caller never fabricates a complete audio map.
    """
    assignments = {key: [] for key in anchors_by_audio}
    unresolved = []
    seen = set()
    for unit in units:
        uid = str(unit.get('unit_id') or '')
        numbers = set(unit.get('numbers') or [])
        if not uid or uid in seen or not numbers:
            raise ValueError('听力单元缺少唯一 ID 或题号')
        seen.add(uid)
        candidates = [key for key, anchors in anchors_by_audio.items()
                      if any(anchors.get(number) for number in numbers)]
        if len(candidates) != 1:
            unresolved.append({'unit_id': uid, 'reason': 'ambiguous' if candidates else 'missing',
                               'candidates': candidates})
            continue
        key = candidates[0]
        anchored = anchors_by_audio[key]
        # A single incidental mention cannot establish ownership of a group.
        # Require every numbered child to have an unambiguous ordered cue.
        cues = [anchored.get(number) or [] for number in sorted(numbers)]
        if any(len(times) != 1 for times in cues):
            unresolved.append({'unit_id': uid, 'reason': 'incomplete_or_repeated', 'candidates': candidates})
            continue
        times = [values[0] for values in cues]
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in times) or any(
                left >= right for left, right in zip(times, times[1:])):
            unresolved.append({'unit_id': uid, 'reason': 'out_of_order', 'candidates': candidates})
            continue
        assignments[key].append(unit)
    return {'assignments': assignments, 'unresolved': unresolved,
            'complete': bool(units) and not unresolved}


def inspect_multiple_audio(batch: Path, inputs: dict, cfg: dict, progress=None) -> dict:
    """Transcribe each recording independently and persist provisional assignment evidence."""
    batch = Path(batch).resolve()
    unit_binding, units = listening_context(batch)
    sources = [(key, item) for key, item in inputs.items()
               if (key == 'audio' or re.fullmatch(r'audio_\d{3,}', key)) and isinstance(item, dict)]
    sources.sort(key=lambda row: row[0])
    if len(sources) < 2 or not units:
        raise RuntimeError('多音频分析需要至少两份音频及已确认的听力题组')
    analysis = batch / '02_audio' / 'sources'
    anchors_by_audio = {}
    source_info = {}
    for key, item in sources:
        path = Path(str(item.get('path') or '')).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError('音频附件不存在：' + key)
        digest = _file_digest(path)
        if item.get('sha256') and item['sha256'] != digest:
            raise RuntimeError('音频附件与批次清单不一致：' + key)
        duration = probe_duration(path)
        silences = detect_silences(path, cfg['noise'], cfg['cut_silence'])
        cuts = skeleton(silences, float(cfg['skeleton_silence']), duration)
        regions = [{'start': a, 'end': b} for a, b in zip(cuts, cuts[1:]) if b > a]
        target = analysis / key / TRANSCRIPT_NAME
        if progress:
            progress('transcribe', 35, '正在分别转写音频 ' + key)
        transcript = transcribe(path, cfg, target, language='auto', regions=regions, progress=progress)
        anchors, coarse = transcript_anchors(transcript, units)
        anchors_by_audio[key] = anchors
        source_info[key] = {'path': str(path), 'sha256': digest, 'duration': duration,
                            'transcript': target.relative_to(batch).as_posix(),
                            'anchors': {str(number): times for number, times in anchors.items()},
                            'coarse_numbers': sorted(coarse), 'silence_skeleton': cuts}
    mapping = assign_audio_units(units, anchors_by_audio)
    assert_current(batch, unit_binding)
    report = {'unit_binding': unit_binding, 'sources': source_info, 'assignments': {key: [unit['unit_id'] for unit in values]
                                                       for key, values in mapping['assignments'].items()},
              'unresolved': mapping['unresolved'], 'complete': mapping['complete'],
              'status': 'provisional', 'note': '只记录题号对应证据，尚未生成正式切片映射'}
    _atomic_json(batch / '02_audio' / 'multi-audio-assignment.json', report)
    return report


def slice_multiple_audio(batch: Path, analysis: dict, progress=None) -> dict:
    """Cut a complete provisional assignment into generation-specific candidates.

    Offsets in each row are local to its recorded source. The assignment is
    rederived and source digests checked before any aggregate is published.
    """
    with _batch_segment_lock(Path(batch)):
        return _slice_multiple_audio_locked(Path(batch).resolve(), analysis, progress)


def _slice_multiple_audio_locked(batch: Path, analysis: dict, progress=None) -> dict:
    unit_binding, units = listening_context(batch)
    sources = analysis.get('sources') if isinstance(analysis, dict) else None
    if (not isinstance(sources, dict) or len(sources) < 2 or
            analysis.get('complete') is not True or analysis.get('unresolved') or
            analysis.get('status') != 'provisional'):
        raise RuntimeError('多音频对应不完整或存在歧义')
    assert_current(batch, analysis.get('unit_binding'))
    manifest_path = batch / 'workflow' / 'manifest.json'
    if not manifest_path.is_file():
        manifest_path = batch / 'manifest.json'
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        inputs = manifest_data['inputs']
        # Progress/other label deliveries legitimately change while cuts run.
        # Preserve the full original input records and batch identity instead.
        manifest_identity = {key: manifest_data.get(key) for key in ('workflow_id', 'created_at', 'inputs')}
        batch_identity = (batch.stat().st_dev, batch.stat().st_ino)
        recorded = {key: value for key, value in inputs.items()
                    if key == 'audio' or re.fullmatch(r'audio_\d{3,}', key)}
        if set(recorded) != set(sources):
            raise ValueError('source set changed')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError('多音频批次来源清单无效') from exc
    anchors_by_audio, paths = {}, {}
    transcript_digests = {}
    for key, source in sources.items():
        if not isinstance(source, dict) or not isinstance(source.get('anchors'), dict):
            raise RuntimeError('多音频来源证据无效')
        path = Path(str(source.get('path') or '')).expanduser().resolve()
        digest = source.get('sha256')
        entry = recorded[key]
        if (not isinstance(entry, dict) or not entry.get('path') or
                Path(entry['path']).expanduser().resolve() != path or
                (entry.get('sha256') and entry['sha256'] != digest) or
                not path.is_file() or not digest or _file_digest(path) != digest):
            raise RuntimeError('多音频来源缺失或摘要已改变：' + str(key))
        duration, cuts = source.get('duration'), source.get('silence_skeleton')
        if (not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0 or
                not isinstance(cuts, list) or len(cuts) < 2 or
                any(not isinstance(c, (int, float)) or not math.isfinite(c) for c in cuts) or
                cuts[0] != 0 or abs(cuts[-1] - duration) > .0011 or
                any(a >= b for a, b in zip(cuts, cuts[1:]))):
            raise RuntimeError('多音频时长或静音骨架无效：' + str(key))
        try:
            anchors = {int(number): times for number, times in source['anchors'].items()}
            if any(not isinstance(times, list) or any(
                    not isinstance(t, (int, float)) or not math.isfinite(t) or not 0 <= t < duration
                    for t in times) for times in anchors.values()):
                raise ValueError('invalid anchor')
        except (TypeError, ValueError) as exc:
            raise RuntimeError('多音频锚点无效：' + str(key)) from exc
        transcript = source.get('transcript')
        transcript_path = (batch / str(transcript or '')).resolve()
        if not transcript or not transcript_path.is_relative_to(batch) or not transcript_path.is_file():
            raise RuntimeError('多音频转写缺失：' + str(key))
        try:
            transcript_data = json.loads(transcript_path.read_text(encoding='utf-8'))
            sidecar = json.loads(transcript_path.with_name(transcript_path.name + '.key.json').read_text(encoding='utf-8'))
            transcript_digest = _file_digest(transcript_path)
            if sidecar['key']['audio_sha256'] != digest or sidecar['sha256'] != transcript_digest:
                raise ValueError('transcript provenance mismatch')
            actual_anchors, actual_coarse = transcript_anchors(transcript_data, units)
            if actual_anchors != anchors or sorted(actual_coarse) != sorted(source.get('coarse_numbers') or []):
                raise ValueError('transcript anchors changed')
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimeError('多音频转写来源或锚点无效：' + str(key)) from exc
        transcript_digests[key] = transcript_digest
        anchors_by_audio[key], paths[key] = anchors, path
    mapping = assign_audio_units(units, anchors_by_audio)
    expected = {key: [unit['unit_id'] for unit in values] for key, values in mapping['assignments'].items()}
    if not mapping['complete'] or expected != analysis.get('assignments'):
        raise RuntimeError('多音频对应已改变或存在歧义')

    out_dir = batch / '02_audio'
    out_dir.mkdir(parents=True, exist_ok=True)
    generation = uuid.uuid4().hex
    items = []
    for source_key, source in sources.items():
        rows = plan(mapping['assignments'][source_key], anchors_by_audio[source_key],
                    source['silence_skeleton'], source['duration'],
                    coarse_numbers=set(source.get('coarse_numbers') or []))
        for row in rows:
            # cut() names by unit_id; add an independent generation and ordinal
            # so sanitized IDs cannot collide or overwrite a previous candidate.
            cut_id = generation + '-' + str(len(items))
            cut_row = dict(row, unit_id=cut_id, audio_sha256=source['sha256'], unit_binding=unit_binding)
            evidence = cut(paths[source_key], out_dir, cut_row)
            file = '02_audio/seg-' + cut_id + '.mp3'
            output = batch / file
            if not output.is_file() or output.stat().st_size == 0 or not evidence:
                raise RuntimeError('多音频切片或证据无效：' + row['unit_id'])
            verified = bool(row.get('boundary_verified'))
            if not verified:
                evidence = [*evidence, '边界证据不足：' + '、'.join(row.get('review_reasons') or [])]
            items.append({'unit_id': row['unit_id'], 'file': file,
                          'source_audio': source_key, 'source_path': str(paths[source_key]),
                          'source_sha256': source['sha256'], 'start': row['start'], 'end': row['end'],
                          'duration': row['duration'], 'confidence': row['confidence'],
                          'quality': row['quality'], 'verified': verified, 'semantic_verified': False,
                          'review_reasons': row['review_reasons'], 'evidence': evidence})
            if progress:
                progress('cut', 75 + int(20 * len(items) / len(units)), row['unit_id'])
    if len(items) != len(units) or {item['unit_id'] for item in items} != {unit['unit_id'] for unit in units}:
        raise RuntimeError('多音频切片覆盖不完整')
    current_manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if ({key: current_manifest.get(key) for key in manifest_identity} != manifest_identity
            or (batch.stat().st_dev, batch.stat().st_ino) != batch_identity
            or current_manifest.get('status') in ('stopped', 'cancelled', 'archived')):
        raise RuntimeError('切片期间批次来源清单或身份已改变')
    for key, path in paths.items():
        if (_file_digest(path) != sources[key]['sha256'] or
                _file_digest(batch / sources[key]['transcript']) != transcript_digests[key]):
            raise RuntimeError('切片期间音频或转写来源已改变：' + str(key))
    assert_current(batch, unit_binding)
    order = {unit['unit_id']: index for index, unit in enumerate(units)}
    items.sort(key=lambda item: order[item['unit_id']])
    for item in items:
        item['file_sha256'] = _file_digest(batch / item['file'])
    needs_review = [item['unit_id'] for item in items if not item['verified']]
    evidence_doc = {'generator': 'audio.segment/multi-v1', 'generation': generation,
                    'unit_binding': unit_binding, 'sources': sources,
                    'needs_review_units': needs_review, 'items': items}
    map_doc = {'generation': generation, 'unit_binding': unit_binding, 'items': [
        {key: item[key] for key in ('unit_id', 'file', 'source_audio', 'source_path',
                                     'source_sha256', 'start', 'end')} for item in items]}
    _atomic_json(out_dir / MAP_NAME, map_doc)
    _atomic_json(out_dir / EVIDENCE_NAME, evidence_doc)
    return {'ok': not needs_review, 'execution_completed': True, 'units': len(items),
            'needs_review_units': needs_review, 'evidence': str(out_dir / EVIDENCE_NAME),
            'generation': generation}


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


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == encoded:
        return
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix="." + path.name, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _transcript_key(audio: Path, cfg: dict, language: str = "en", align: bool = True, regions=None) -> dict:
    """Content identity, worker contract and semantic settings, never directory or mtime."""
    return {"schema": 3, "audio_sha256": _file_digest(audio),
            "worker_sha256": _file_digest(bootstrap.worker_path()),
            "asr_model": cfg.get("asr_model"),
            "align_model": cfg.get("align_model") if align else None,
            "vad_method": cfg.get("vad_method"), "language": language, "align": bool(align),
            "regions": regions}


def _transcript_is_fresh(out: Path, key: dict) -> bool:
    try:
        stored = json.loads(out.with_name(out.name + ".key.json").read_text(encoding="utf-8"))
        if stored.get("key") != key or stored.get("sha256") != _file_digest(out):
            return False
        data = json.loads(out.read_text(encoding="utf-8"))
        return (data.get("schema_version") == 2 and data.get("segments")
                and data.get("timestamp_precision") in ("word", "mixed", "segment"))
    except (OSError, ValueError, AttributeError):
        return False


def transcribe(audio: Path, cfg: dict, out: Path, language: str = "en",
               align: bool = True, regions=None, progress=None) -> dict:
    key = _transcript_key(audio, cfg, language, align, regions)
    cache_id = hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()
    shared = cfgmod.data_dir(cfg) / "transcript-cache" / cache_id / "transcript.json"
    def install_cache(target, data):
        _atomic_json(target, data)
        _atomic_json(target.with_name(target.name + ".key.json"),
                     {"key": key, "sha256": _file_digest(target)})
    if _transcript_is_fresh(out, key):
        data = json.loads(out.read_text(encoding="utf-8"))
        install_cache(shared, data)
        return data
    if _transcript_is_fresh(shared, key):
        data = json.loads(shared.read_text(encoding="utf-8"))
        install_cache(out, data)
        return data
    py = bootstrap.venv_python(cfg)
    if not py.is_file():
        raise RuntimeError("尚未安装 WhisperX 运行时，请先安装运行时与模型")
    # Worker writes into an isolated temporary directory: interrupted calls never
    # destroy the prior valid transcript or create a half-written cache hit.
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".transcribe-", dir=out.parent) as temporary:
        candidate = Path(temporary) / "transcript.json"
        argv = [str(py), str(bootstrap.worker_path()), "transcribe",
                "--audio", str(audio), "--out", str(candidate),
                "--asr", str(cfg["asr_model"]), "--align", str(cfg["align_model"]),
                "--device", str(cfg["device"]), "--compute-type", str(cfg["compute_type"]),
                "--vad-method", str(cfg["vad_method"]), "--language", language,
                "--model-dir", str(bootstrap.models_dir(cfg))]
        if regions is not None:
            regions_file = Path(temporary) / "regions.json"
            _atomic_json(regions_file, regions)
            argv.extend(["--regions", str(regions_file)])
        if not align:
            argv.append("--no-align")
        try:
            process = _run(argv, env=bootstrap.runtime_env(cfg), on_progress=(
                (lambda event: progress("transcribe", 35, str(event["detail"])[:200])) if progress else None))
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "") + "\n" + (exc.stdout or "")
            out.with_name(out.name + ".worker.log").write_text(detail, encoding="utf-8")
            raise RuntimeError("转写失败，旧缓存保留：" + detail[-1200:]) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("转写超过30分钟预算，已终止子进程；旧缓存保留，可重试") from exc
        if process is not None:
            out.with_name(out.name + ".worker.log").write_text(
                (getattr(process, "stderr", "") or "") + "\n" + (getattr(process, "stdout", "") or ""),
                encoding="utf-8")
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if data.get("schema_version") != 2 or not data.get("segments"):
            raise RuntimeError("转写没有真实时间段，保留旧转写并等待重试")
        _atomic_json(out, data)
    _atomic_json(out.with_name(out.name + ".key.json"),
                 {"key": key, "sha256": _file_digest(out)})
    install_cache(shared, data)
    return data


def cut(audio: Path, out_dir: Path, item: dict) -> list[str]:
    """Atomic exact re-encode; unchanged audio/bounds reuse byte-verified cut cache."""
    name = "seg-" + re.sub(r"[^A-Za-z0-9._-]", "-", str(item["unit_id"])) + ".mp3"
    target = out_dir / name
    sidecar = out_dir / (name + ".key.json")
    key = {"version": 3, "audio": item.get("audio_sha256") or _file_digest(audio),
           "unit_binding": item.get("unit_binding"),
           "start": item["start"], "end": item["end"], "codec": "libmp3lame-q4"}
    try:
        cached = json.loads(sidecar.read_text(encoding="utf-8"))
        if cached.get("key") == key and cached.get("sha256") == _file_digest(target):
            return list(cached["evidence"])
    except (OSError, ValueError, KeyError):
        pass
    length = round(item["end"] - item["start"], 3)
    if length <= 0:
        raise RuntimeError("切片区间非正数：" + str(item["unit_id"]))
    with tempfile.TemporaryDirectory(prefix=".cut-", dir=out_dir) as temporary:
        candidate = Path(temporary) / name
        command = [bootstrap.tool_path("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-ss", str(item["start"]), "-i", str(audio), "-t", str(length),
                   "-c:a", "libmp3lame", "-q:a", "4", str(candidate)]
        process = subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if process.returncode or not candidate.is_file() or candidate.stat().st_size == 0:
            raise RuntimeError("切片失败 " + str(item["unit_id"]) + ": " + (process.stderr or "")[-400:])
        os.replace(candidate, target)
    evidence = ["ffmpeg libmp3lame exact re-encode", "audio_sha256=" + key["audio"],
                f"整卷偏移 {item['start']}s → {item['end']}s；锚点仅用于定位，非听音核验"]
    _atomic_json(sidecar, {"key": key, "sha256": _file_digest(target), "evidence": evidence})
    return evidence


@contextmanager
def _batch_segment_lock(batch: Path):
    """Cross-process lock shared by automatic and manual audio segmentation."""
    lock_path = Path(batch) / "02_audio" / ".segment.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write("0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def lock_is_held(batch: Path) -> bool:
    """不阻塞地探测本批次切段锁是否仍被活着的进程持有。

    状态文件能活过后端重启（进程被强杀时 finally 不会执行），所以「running=True」
    不等于真在跑；锁才是权威证据。POSIX 用 flock、Windows 用 msvcrt 字节锁，
    与 _batch_segment_lock 的加锁方式保持一致。
    """
    path = Path(batch) / "02_audio" / ".segment.lock"
    if not path.is_file():
        return False
    try:
        handle = path.open("r+b")
    except OSError:
        # 打不开（例如文件被独占）说明另一个进程正在用它。
        return True
    with handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False


def run(batch: Path, cfg: dict | None = None, audio: str | None = None,
        progress=None) -> dict:
    with _batch_segment_lock(Path(batch)):
        return _run_locked(batch, cfg, audio, progress)


def _run_locked(batch: Path, cfg: dict | None = None, audio: str | None = None,
        progress=None) -> dict:
    """执行完整切段；返回摘要。progress(phase, pct, detail) 可选。"""
    cfg = cfg or cfgmod.load()
    started = time.monotonic()
    wall_started = time.time()
    timings = {}
    def measured(name, function, *args, **kwargs):
        tick = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            timings[name] = round(time.monotonic() - tick, 4)
    batch = Path(batch).resolve()
    out_dir = batch / "02_audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda *_: None)

    manifest_path = batch / 'workflow/manifest.json'
    if manifest_path.is_file():
        try:
            inputs = json.loads(manifest_path.read_text(encoding='utf-8')).get('inputs', {})
        except (OSError, ValueError, AttributeError) as exc:
            raise RuntimeError('无法读取批次音频清单') from exc
        if any(re.fullmatch(r'audio_\d{3,}', key) and isinstance(item, dict) and item.get('exists')
               for key, item in inputs.items()):
            raise RuntimeError('多份音频已保存，但尚未完成音频与题组的对应，不能按单音频切段')
    unit_binding, units = listening_context(batch)
    if not units:
        raise RuntimeError("该批次没有听力单元，无需切段")
    src = find_audio(batch, audio)
    audio_sha256 = _file_digest(src)
    report("probe", 5, "读取音频时长")
    duration = measured("probe", probe_duration, src)
    report("silence", 15, "silencedetect 定骨架")
    silences = measured("silence_detection", detect_silences, src, cfg["noise"], cfg["cut_silence"])
    cuts = skeleton(silences, float(cfg["skeleton_silence"]), duration)
    fine_cuts = fine_silence_cuts(silences, float(cfg["cut_silence"]),
                                  float(cfg["skeleton_silence"]), duration)
    report("units", 25, "读取听力单元")
    assert_current(batch, unit_binding)

    # Split only at measured long-silence midpoints BEFORE ASR, not after
    # drifting alignment has merged separate questions into a 30-second chunk.
    regions = [{"start": a, "end": b} for a, b in zip(cuts, cuts[1:]) if b > a]
    transcript_path = out_dir / TRANSCRIPT_NAME
    announce_path = out_dir / ANNOUNCE_NAME
    anchor_language = str(cfg.get("anchor_language") or "").strip()
    need_announce = bool(anchor_language) and anchor_language != "en"

    # 两遍转写互相独立：英文（正文 + 词级对齐）与题号播报（只取锚点、不对齐）。
    # 并行跑，墙钟≈较慢的那一遍，而不是两遍相加；任一遍失败都不拖垮整批。
    report("transcribe", 35, "WhisperX 大模型转写（受资源预算约束；缓存按内容复用）")
    transcript = announce = None
    transcript_error = announce_error = None
    if need_announce and int(cfg.get("max_parallel_transcriptions", 1)) > 1:
        import concurrent.futures as _futures
        with _futures.ThreadPoolExecutor(max_workers=max(1, min(2, int(cfg.get("max_parallel_transcriptions", 1))))) as pool:
            main_future = pool.submit(measured, "english_transcription", transcribe, src, cfg, transcript_path, language="auto", regions=regions, progress=report)
            announce_future = pool.submit(measured, "announcement_transcription", transcribe, src, cfg, announce_path,
                                          language=anchor_language, align=False, regions=regions, progress=report)
            try:
                transcript = main_future.result()
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
                transcript_error = exc
            try:
                announce = announce_future.result()
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
                announce_error = exc
    else:
        try:
            transcript = measured("english_transcription", transcribe, src, cfg, transcript_path, language="auto", regions=regions, progress=report)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            transcript_error = exc
        if need_announce and (transcript is None or not _has_section_coverage(transcript, units)):
            try:
                announce = measured("announcement_transcription", transcribe, src, cfg,
                                    announce_path, language=anchor_language, align=False, regions=regions, progress=report)
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
                announce_error = exc
        elif need_announce:
            timings["announcement_skipped"] = "primary_has_all_section_titles"
    if transcript is None and announce is None:
        raise RuntimeError("两遍转写都失败：" + str(transcript_error or announce_error))
    if transcript is None:
        # 正文转写失败但播报可用：只用锚点，正文留给人工（切段仍能完成）。
        report("transcribe", 60, "正文转写失败，仅用题号播报锚点：" + str(transcript_error)[:100])
        transcript = {"words": [], "text": ""}
    anchors, coarse_numbers = transcript_anchors(transcript, units)
    if announce is not None:
        extra, extra_coarse = transcript_anchors(announce, units)
        for number, times in extra.items():
            if number not in anchors:
                anchors[number] = times
                if number in extra_coarse:
                    coarse_numbers.add(number)
    elif announce_error is not None:
        report("plan", 62, "题号播报转写失败，保留正文结果：" + str(announce_error)[:100])
    anchors = _merge_anchors(anchors)
    regional, region_proofs = region_bound_anchors(transcript, units, cuts, anchors)
    for number, times in regional.items():
        anchors[number] = times
        coarse_numbers.discard(number)
    coarse_numbers.intersection_update(anchors)
    report("plan", 75, f"骨架 {len(cuts) - 2} 个切点、细静音 {len(fine_cuts)} 个，"
                       f"锚点 {sum(len(v) for v in anchors.values())} 个")
    plan_rows = measured("planning", plan, units, anchors, cuts, duration, fine_cuts, coarse_numbers)

    if _file_digest(src) != audio_sha256:
        raise RuntimeError("转写期间音频来源已改变")
    assert_current(batch, unit_binding)
    items = []
    # 切片是纯 ffmpeg 子进程，互相独立：并行切，墙钟从「段数 × 单段耗时」降到
    # 大致「单段耗时 × 批次数」。失败仍然按单元抛出，不影响其它段的记录。
    import concurrent.futures as _cut_futures
    workers = max(1, min(4, (os.cpu_count() or 2)))
    cut_started = time.monotonic()
    cut_results: dict[int, list[str]] = {}
    with _cut_futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(cut, src, out_dir, dict(row, audio_sha256=audio_sha256, unit_binding=unit_binding)): index
                   for index, row in enumerate(plan_rows)}
        for future in _cut_futures.as_completed(futures):
            cut_results[futures[future]] = future.result()
    timings["cutting"] = round(time.monotonic() - cut_started, 4)
    for i, row in enumerate(plan_rows):
        evidence = cut_results[i]
        # 微段（被挤进同一骨架切点之间，时长过短）不标 verified：避免把错误的
        # 0.5s 片段冒充“已核验切片”喂给 audio 阶段的逐字校验。切片仍写进
        # audio_map 供人工回听修正，但证据里如实标出问题。
        verified = bool(row.get("boundary_verified")) and transcript_error is None
        if not verified:
            evidence = [*evidence, "边界证据不足：" + "、".join(row.get("review_reasons") or []) + "；候选已保留，不冒充核验通过"]
        items.append({"unit_id": row["unit_id"],
                      "file": "02_audio/" + "seg-" + re.sub(r"[^A-Za-z0-9._-]", "-", str(row["unit_id"])) + ".mp3",
                      "start": row["start"], "end": row["end"], "duration": row.get("duration"),
                      "confidence": row.get("confidence"), "quality": row.get("quality"),
                      "verified": verified, "semantic_verified": False,
                      "review_reasons": row.get("review_reasons", []), "evidence": evidence})
        report("cut", 75 + int(20 * (i + 1) / len(plan_rows)), row["unit_id"])

    for item in items:
        item["file_sha256"] = _file_digest(batch / item["file"])
    needs_review = [it["unit_id"] for it in items if not it["verified"]]
    assert_current(batch, unit_binding)
    if _file_digest(src) != audio_sha256:
        raise RuntimeError("切片期间音频来源已改变")
    evidence_doc = {"generator": "audio.segment/v2", "audio": str(src),
                    "unit_binding": unit_binding, "audio_sha256": audio_sha256,
                    "duration": duration, "config": cfg,
                    "silence_skeleton": cuts, "anchors": {str(k): v for k, v in anchors.items()},
                    "needs_review_units": needs_review, "region_proofs": {str(n): v for n,v in region_proofs.items()},
                    "items": items}
    _atomic_json(out_dir / EVIDENCE_NAME, evidence_doc)
    _atomic_json(out_dir / MAP_NAME, {"unit_binding": unit_binding, "items": [
        {"unit_id": it["unit_id"], "file": it["file"], "start": it["start"], "end": it["end"]}
        for it in items]})
    if needs_review:
        report("warn", 99, "以下单元边界不可信，需人工回听：" + "、".join(needs_review[:12]))
    timings["wall_seconds"] = round(time.monotonic() - started, 4)
    timing_path = out_dir / "timing.json"
    _atomic_json(timing_path, {"started_at_epoch": wall_started, "finished_at_epoch": time.time(),
                 "stages": timings, "asr_model": cfg["asr_model"],
                 "max_parallel_transcriptions": cfg.get("max_parallel_transcriptions", 1),
                 "semantic_verification": "not_performed", "needs_review_units": needs_review})
    report("done", 100, f"已生成 {len(items)} 段，{len(needs_review)} 段待核对")
    return {"ok": not needs_review, "execution_completed": True, "units": len(items), "duration": duration,
            "skeleton_cuts": len(cuts) - 2, "anchors": sum(len(v) for v in anchors.values()),
            "needs_review_units": needs_review, "evidence": str(out_dir / EVIDENCE_NAME)}
