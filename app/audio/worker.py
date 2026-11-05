# -*- coding: utf-8 -*-
"""在独立 venv 中预下载模型 / 转写；主进程不必安装 ML 依赖。

无对齐时只输出真实 ASR 段时间，禁止按文字长度制造词级时间。
  python worker.py capabilities --model-dir <models>   # 不下载、不推理
  python worker.py bootstrap --model-dir <models>
  python worker.py transcribe --audio <wav> --out <transcript.json> --model-dir <models>
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import importlib.metadata
import inspect
import json
import math
import os
import sys
import time
from pathlib import Path

SCHEMA_VERSION = 2
ALIGNMENT_VALIDATION_VERSION = 2
SAMPLE_RATE = 16000  # whisperx.load_audio 的固定输出采样率。


def _contains_cjk(text):
    """English 字典不提供汉字/假名/韩文的真实词级声学对齐。"""
    return any(0x3400 <= ord(char) <= 0x9FFF or 0xF900 <= ord(char) <= 0xFAFF
               or 0x20000 <= ord(char) <= 0x323AF or 0x3040 <= ord(char) <= 0x30FF
               or 0xAC00 <= ord(char) <= 0xD7AF for char in str(text or ""))


def progress(phase, detail):
    print(json.dumps({"phase": phase, "detail": detail}, ensure_ascii=False), flush=True)


def common(args):
    import whisperx
    return whisperx


def capabilities(args):
    """只验证本地依赖/别名/计算类型，不实例化模型或访问模型缓存。"""
    from faster_whisper.utils import available_models
    from whisperx.asr import load_model
    import ctranslate2

    models = available_models()
    if args.asr not in models:
        raise RuntimeError(f"本地 faster-whisper 不支持模型 {args.asr}；请升级依赖，不会降级为 small")
    if "threads" not in inspect.signature(load_model).parameters:
        raise RuntimeError("本地 WhisperX 不支持 threads 参数，请升级依赖以落实 CPU 线程预算")
    compute_types = sorted(ctranslate2.get_supported_compute_types(args.device))
    if args.compute_type not in compute_types:
        raise RuntimeError(f"本地 {args.device} 不支持 {args.compute_type}；支持：{', '.join(compute_types)}")
    versions = {name: importlib.metadata.version(name) for name in
                ("whisperx", "faster-whisper", "ctranslate2", "torch", "transformers")}
    return {"asr_model": args.asr, "model_alias_supported": True,
            "threads_supported": True, "device": args.device,
            "compute_type": args.compute_type, "supported_compute_types": compute_types,
            "versions": versions, "model_loaded": False}


def _configure_threads(args):
    # 在导入 numpy/torch/WhisperX 前限制本地线程池。
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[key] = str(args.cpu_threads)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def _configure_torch(args):
    import torch
    torch.set_num_threads(args.cpu_threads)
    # worker 是独立进程；此处尚未执行任何模型。
    torch.set_num_interop_threads(1)


@contextmanager
def model_slot(model_dir, max_parallel):
    """跨 worker 进程限制模型驻留数；异常退出由 OS 释放锁，无陈旧 PID 锁。

    同一模型缓存共享预算。不要删除锁文件：unlink 会使并发进程锁住不同 inode。
    """
    root = Path(model_dir)
    root.mkdir(parents=True, exist_ok=True)
    handles = []
    acquired = None
    if os.name == "nt":
        import msvcrt
        def lock(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        def unlock(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        def lock(handle):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        def unlock(handle):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    try:
        for index in range(max_parallel):
            handle = (root / f".worker-slot-{index}.lock").open("a+b")
            if os.name == "nt" and handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handles.append(handle)
        waiting_reported = False
        while acquired is None:
            for handle in handles:
                try:
                    lock(handle)
                    acquired = handle
                    break
                except OSError as exc:
                    import errno
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
            if acquired is None:
                if not waiting_reported:
                    progress("queue", "等待模型资源槽（并行上限 " + str(max_parallel) + "）")
                    waiting_reported = True
                time.sleep(0.1)
        yield
    finally:
        if acquired is not None:
            unlock(acquired)
        for handle in handles:
            handle.close()


def _load_asr(whisperx, args, language, local_files_only):
    return whisperx.load_model(args.asr, device=args.device, compute_type=args.compute_type,
                               download_root=args.model_dir, vad_method=args.vad_method,
                               language=language, local_files_only=local_files_only,
                               threads=args.cpu_threads)


def cmd_bootstrap(args):
    progress("capabilities", capabilities(args))
    _configure_torch(args)
    whisperx = common(args)
    progress("asr", "下载 ASR 模型 " + args.asr)
    model = _load_asr(whisperx, args, "en", False)
    del model
    progress("align", "下载对齐模型 " + args.align)
    align_model, _ = whisperx.load_align_model(language_code="en", device=args.device,
                                               model_name=args.align, model_dir=args.model_dir)
    del align_model
    progress("done", "模型已缓存到 " + args.model_dir)


def _bounds(item):
    try:
        start, end = item["start"], item["end"]
        if isinstance(start, bool) or isinstance(end, bool):
            return None
        start, end = float(start), float(end)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            return None
        return start, end
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _segments_to_words(segments):
    """兼容旧调用名：没有真实对齐时不生成任何词级时间戳。"""
    return []


def _source_segments(segments, diagnostics):
    output = []
    for index, source in enumerate(segments or []):
        if not isinstance(source, dict):
            diagnostics.append({"code": "invalid_asr_segment", "segment_index": index})
            continue
        segment = deepcopy(source)
        segment["words"] = []
        segment["timestamp_precision"] = "segment"
        if _bounds(segment) is None:
            diagnostics.append({"code": "invalid_asr_bounds", "segment_index": index})
            # 非有限数字不是合法 JSON 时间，保留段和文本但显式置空非法坐标。
            for key in ("start", "end"):
                value = segment.get(key)
                if isinstance(value, float) and not math.isfinite(value):
                    segment[key] = None
        output.append(segment)
    return output


def _validated_alignment(segments, aligned, diagnostics):
    """按原段逐段校验：不让 alignment 的漂移覆盖邻段或改写 ASR 段边界。"""
    # 允许调用方把已输出 segments 同时作为来源和候选；清空前复制，防止别名污染。
    candidates = deepcopy(aligned.get("segments", [])) if isinstance(aligned, dict) else []
    for segment in segments:
        segment["words"] = []
        segment["timestamp_precision"] = "segment"
    if not isinstance(candidates, list) or len(candidates) != len(segments):
        diagnostics.append({"code": "alignment_segment_count_mismatch"})
        return []
    words = []
    bounds = [_bounds(segment) for segment in segments]
    for index, (segment, candidate) in enumerate(zip(segments, candidates)):
        original = bounds[index]
        proposed = candidate.get("words") if isinstance(candidate, dict) else None
        reason = None
        rejection_detail = {}
        accepted = []
        if original is None:
            reason = "invalid_source_bounds"
        elif str(segment.get("language") or "en") != "en":
            reason = "alignment_language_unsupported"
        elif _contains_cjk(segment.get("text")) or (isinstance(proposed, list) and any(
                isinstance(word, dict) and _contains_cjk(word.get("word")) for word in proposed)):
            # 混合段仍保留真实段时间；不要求整份 ASR 转写都是英语。
            reason = "english_alignment_contains_cjk"
        elif not isinstance(proposed, list) or not proposed:
            reason = "missing_word_timestamps"
        else:
            previous_end = original[0]
            for word in proposed:
                timed = _bounds(word) if isinstance(word, dict) else None
                if timed is None:
                    reason = "invalid_word_bounds"
                    break
                start, end = timed
                if start < original[0] or end > original[1]:
                    reason = "word_outside_source_segment"
                    break
                if start < previous_end:
                    reason = "word_order_or_overlap"
                    break
                if any(other is not None and other_index != index
                       and start < other[1] and end > other[0]
                       for other_index, other in enumerate(bounds)):
                    reason = "word_overlaps_other_segment"
                    break
                text = str(word.get("word") or "").strip()
                if not text:
                    reason = "empty_aligned_word"
                    break
                accepted_word = {"word": text, "start": start, "end": end}
                if "region_id" in segment:
                    accepted_word["region_id"] = segment["region_id"]
                accepted.append(accepted_word)
                previous_end = end
            if not reason:
                # 丢掉不可对齐词/句也不能把其余词伪装为完整词级对齐。
                normalized_words = "".join("".join(w["word"].split()) for w in accepted)
                normalized_text = "".join(str(segment.get("text") or "").split())
                if normalized_words != normalized_text:
                    reason = "aligned_text_mismatch"
            if not reason:
                tolerance = max(2.0, 0.2 * (original[1] - original[0]))
                start_gap = accepted[0]["start"] - original[0]
                end_gap = original[1] - accepted[-1]["end"]
                if start_gap > tolerance or end_gap > tolerance:
                    reason = "alignment_compressed"
                    rejection_detail = {"start_gap": round(start_gap, 6),
                                        "end_gap": round(end_gap, 6),
                                        "boundary_tolerance": round(tolerance, 6)}
        if reason:
            diagnostics.append({"code": "alignment_rejected", "segment_index": index,
                                "reason": reason, **rejection_detail})
        else:
            segment["words"] = accepted
            segment["timestamp_precision"] = "word"
            words.extend(accepted)
    return words


def revalidate_transcript(transcript):
    """纯函数：复核已有 worker 结果，不读文件、不加载模型、不修改输入。

    必须提供保存原 ASR start/end/text 的 segments，并在各段 words 内携带旧候选。
    仅有顶层 words 时无法可靠恢复来源，不猜配对，直接保留段级时间。
    返回完整副本，重建 words/precision/diagnostics 并标注校验规则版本。
    """
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        raise ValueError("重检必须提供带原始 ASR 边界的 segments")
    output = deepcopy(transcript)
    diagnostics = deepcopy(transcript.get("diagnostics") or [])
    proposals = deepcopy(transcript["segments"])
    segments = _source_segments(transcript["segments"], diagnostics)
    words = _validated_alignment(segments, {"segments": proposals}, diagnostics)
    precision = "segment"
    if words:
        precision = "word" if all(s["timestamp_precision"] == "word" for s in segments) else "mixed"
    output.update(schema_version=SCHEMA_VERSION, alignment_validation_version=ALIGNMENT_VALIDATION_VERSION,
                  segments=segments, words=words, timestamp_precision=precision, diagnostics=diagnostics)
    return output


def _parse_regions(value):
    """只接收调用方提供的真实静音边界，不从题数/文本推断或等分。"""
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("regions 不能为空")
        raw = value if value.lstrip().startswith(("[", "{")) else Path(value).read_text(encoding="utf-8")
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ValueError("regions 必须是有效 JSON 数组") from exc
    if not isinstance(value, list) or not value:
        raise ValueError("regions 必须是非空 JSON 数组")
    regions = []
    previous_end = 0.0
    for index, item in enumerate(value):
        if not isinstance(item, dict) or any(isinstance(item.get(key), bool)
                or not isinstance(item.get(key), (int, float)) for key in ("start", "end")):
            raise ValueError(f"region {index} 的 start/end 必须是有限数字")
        bounds = _bounds(item)
        if bounds is None:
            raise ValueError(f"region {index} 必须满足 0 <= start < end 且时间有限")
        start, end = bounds
        if start < previous_end:
            raise ValueError(f"region {index} 必须有序且不与前一区域重叠")
        regions.append({"region_id": index, "start": start, "end": end})
        previous_end = end
    return regions


def _regions_for_audio(regions, audio):
    """加载模型前验证时长与实际采样范围；offset 必须来自实际 slice 起点。"""
    if regions is None:
        return None
    sample_count = len(audio)
    duration = sample_count / SAMPLE_RATE
    output = []
    for region in regions:
        if region["end"] > duration:
            raise ValueError(f"region {region['region_id']} 超过音频时长 {duration} 秒")
        start_sample = int(region["start"] * SAMPLE_RATE)
        end_sample = int(region["end"] * SAMPLE_RATE)
        if not 0 <= start_sample < end_sample <= sample_count:
            raise ValueError(f"region {region['region_id']} 没有有效非空音频采样范围")
        output.append({**region, "sample_start": start_sample, "sample_end": end_sample})
    return output


def _transcribe_regions(model, audio, regions, batch_size, language, diagnostics):
    """同一个 ASR 实例逐区域运行；只加真实采样 offset，不生成区域内伪切点。"""
    segments = []
    languages = []
    for region in regions:
        region_id = region["region_id"]
        offset = region["sample_start"] / SAMPLE_RATE
        span = (region["sample_end"] - region["sample_start"]) / SAMPLE_RATE
        progress("asr", f"转写区域 {region_id + 1}/{len(regions)}：{region['start']}–{region['end']}s")
        result = model.transcribe(audio[region["sample_start"]:region["sample_end"]],
                                  batch_size=batch_size, language=language)
        detected = str(result.get("language") or language or "")
        local_segments = result.get("segments", []) or []
        if not local_segments:
            diagnostics.append({"code": "empty_region", "region_id": region_id,
                                "start": region["start"], "end": region["end"]})
            continue
        if detected and detected not in languages:
            languages.append(detected)
        for local in local_segments:
            bounds = _bounds(local) if isinstance(local, dict) else None
            if bounds is None or bounds[1] > span:
                diagnostics.append({"code": "region_segment_out_of_bounds", "region_id": region_id})
                continue
            segment = deepcopy(local)
            segment.update(start=bounds[0] + offset, end=bounds[1] + offset,
                           region_id=region_id, language=detected)
            segments.append(segment)
    detected = languages[0] if len(languages) == 1 else "mixed" if languages else language or ""
    return {"language": detected, "segments": segments}


def cmd_transcribe(args):
    # 所有用户区域必须先校验，再允许加载模型；JSON 错误甚至不导入重依赖。
    regions = _parse_regions(getattr(args, "regions", None))
    # 对齐加载器没有 local_files_only 参数，须在导入 HF 依赖前设置离线模式。
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    progress("capabilities", capabilities(args))
    _configure_torch(args)
    whisperx = common(args)
    language = (args.language or "").strip()
    whisper_language = None if language in ("", "auto") else language
    audio = whisperx.load_audio(args.audio)
    regions = _regions_for_audio(regions, audio)
    progress("asr", "加载 " + args.asr)
    # 转写必须使用安装阶段缓存；缺模型时明确失败，不能偷偷下载或换 small。
    model = _load_asr(whisperx, args, whisper_language, True)
    diagnostics = []
    if regions is None:
        progress("asr", "转写中")
        result = model.transcribe(audio, batch_size=args.batch_size, language=whisper_language)
    else:
        result = _transcribe_regions(model, audio, regions, args.batch_size, whisper_language, diagnostics)
    del model
    segments = _source_segments(result.get("segments", []) or [], diagnostics)
    detected = str(result.get("language") or whisper_language or "")
    words = []
    if args.no_align:
        progress("align", "跳过词级对齐，保留真实段级时间")
        diagnostics.append({"code": "alignment_disabled", "detail": "仅提供 ASR 段级时间，不能作为精确词级锚点"})
    elif detected != "en" and not any(segment.get("language") == "en" for segment in segments):
        diagnostics.append({"code": "alignment_language_unsupported", "language": detected,
                            "detail": "配置的 English 对齐模型只用于 en，保留段级时间"})
    else:
        progress("align", "English 词级对齐中")
        try:
            align_model, metadata = whisperx.load_align_model(language_code="en", device=args.device,
                                                               model_name=args.align,
                                                               model_dir=args.model_dir)
            # WhisperX 会把一段拆成多句；逐原段调用以保持来源映射，而非按输出下标猜。
            candidates = []
            for index, segment in enumerate(segments):
                proposed = []
                if (_bounds(segment) is not None and not _contains_cjk(segment.get("text"))
                        and str(segment.get("language") or detected) == "en"):
                    try:
                        aligned = whisperx.align([deepcopy(segment)], align_model, metadata,
                                                 audio, args.device, interpolate_method="ignore",
                                                 return_char_alignments=False)
                        for sentence in aligned.get("segments", []) or []:
                            proposed.extend(sentence.get("words", []) or [])
                    except Exception as exc:
                        diagnostics.append({"code": "alignment_failed", "segment_index": index,
                                            "error_type": type(exc).__name__, "detail": str(exc)[:500]})
                candidates.append({"words": proposed})
            del align_model
            words = _validated_alignment(segments, {"segments": candidates}, diagnostics)
        except Exception as exc:  # 对齐失败仍交付真实 ASR 段，供调用方保守处理。
            diagnostics.append({"code": "alignment_failed", "error_type": type(exc).__name__,
                                "detail": str(exc)[:500]})
    precision = "segment"
    if words:
        precision = "word" if all(s["timestamp_precision"] == "word" for s in segments) else "mixed"
    text = " ".join(str(segment.get("text") or "").strip() for segment in segments)
    out = {"schema_version": SCHEMA_VERSION, "alignment_validation_version": ALIGNMENT_VALIDATION_VERSION,
           "language": detected, "text": text.strip(),
           "words": words, "segments": segments, "timestamp_precision": precision,
           "diagnostics": diagnostics, "regions": regions, "asr_model": args.asr,
           "align_model": "" if args.no_align else args.align,
           "resources": {"cpu_threads": args.cpu_threads, "batch_size": args.batch_size,
                         "max_parallel_transcriptions": args.max_parallel}}
    target = Path(args.out)
    # 不留下可被当成缓存复用的半截 JSON。
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(out, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(target)
    progress("done", f"保留 {len(segments)} 个原始段、{len(words)} 个有效词级时间戳（{precision}）")


def _budget(value, upper):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("资源预算必须为整数") from exc
    if not 1 <= number <= upper:
        raise argparse.ArgumentTypeError(f"资源预算必须在 1~{upper} 之间")
    return number


def main(argv=None):
    p = argparse.ArgumentParser(description="WhisperX 听力切段 worker")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("bootstrap", "transcribe", "capabilities"):
        s = sub.add_parser(name)
        s.add_argument("--asr", default="large-v3-turbo")
        s.add_argument("--align", default="facebook/wav2vec2-base-960h")
        s.add_argument("--device", default="cpu")
        s.add_argument("--compute-type", default="int8")
        s.add_argument("--vad-method", default="pyannote")
        s.add_argument("--model-dir", required=True)
        s.add_argument("--cpu-threads", type=lambda v: _budget(v, 64),
                       default=os.environ.get("WF_AUDIO_CPU_THREADS", "4"))
        s.add_argument("--max-parallel", type=lambda v: _budget(v, 2),
                       default=os.environ.get("WF_AUDIO_MAX_PARALLEL", "1"))
        s.add_argument("--batch-size", type=lambda v: _budget(v, 64),
                       default=os.environ.get("WF_AUDIO_BATCH_SIZE", "2"))
        if name == "transcribe":
            s.add_argument("--audio", required=True)
            s.add_argument("--out", required=True)
            s.add_argument("--regions", default=None,
                           help="长静音中点构成的 [{start,end}] JSON 数组或 JSON 文件；可省略")
            s.add_argument("--language", default="en", help="en/zh/auto；auto 交给 whisper 判定")
            s.add_argument("--no-align", action="store_true", help="只输出真实段时间，words 为空")
    args = p.parse_args(argv)
    _configure_threads(args)
    if args.command == "capabilities":
        progress("capabilities", capabilities(args))
        return 0
    with model_slot(args.model_dir, args.max_parallel):
        if args.command == "bootstrap":
            cmd_bootstrap(args)
        else:
            cmd_transcribe(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
