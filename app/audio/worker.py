# -*- coding: utf-8 -*-
"""在懒引导创建的 venv 里执行：预下载模型 / 转写并做词级对齐。

这个脚本只用标准库启动，重依赖（torch / whisperx / transformers）都在命令内部
按需 import；主进程（插件后端）不需要装任何 ML 依赖。

用法：
  python worker.py bootstrap --asr small.en --align WAV2VEC2_ASR_BASE_960H ...
  python worker.py transcribe --audio <wav> --out <words.json> ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def progress(phase, detail):
    print(json.dumps({"phase": phase, "detail": detail}, ensure_ascii=False), flush=True)


def common(args):
    import whisperx  # noqa: F401 - 仅确认依赖可用
    return whisperx


def cmd_bootstrap(args):
    whisperx = common(args)
    progress("asr", "下载 ASR 模型 " + args.asr)
    model = whisperx.load_model(args.asr, device=args.device, compute_type=args.compute_type,
                                download_root=args.model_dir, vad_method=args.vad_method,
                                language="en", local_files_only=False)
    del model
    progress("align", "下载对齐模型 " + args.align)
    align_model, _ = whisperx.load_align_model(language_code="en", device=args.device,
                                               model_name=args.align, model_dir=args.model_dir)
    del align_model
    progress("done", "模型已缓存到 " + args.model_dir)


def cmd_transcribe(args):
    whisperx = common(args)
    progress("asr", "加载 " + args.asr)
    model = whisperx.load_model(args.asr, device=args.device, compute_type=args.compute_type,
                                download_root=args.model_dir, vad_method=args.vad_method,
                                language="en")
    audio = whisperx.load_audio(args.audio)
    progress("asr", "转写中")
    result = model.transcribe(audio, batch_size=args.batch_size, language="en")
    del model
    progress("align", "词级对齐中")
    align_model, metadata = whisperx.load_align_model(language_code="en", device=args.device,
                                                      model_name=args.align, model_dir=args.model_dir)
    aligned = whisperx.align(result["segments"], align_model, metadata, audio, args.device,
                             return_char_alignments=False)
    words = []
    for seg in aligned.get("segments", []):
        for w in seg.get("words", []) or []:
            if not isinstance(w, dict) or w.get("start") is None or w.get("end") is None:
                continue
            words.append({"word": str(w.get("word") or ""), "start": float(w["start"]), "end": float(w["end"])})
    text = " ".join(str(s.get("text") or "").strip() for s in aligned.get("segments", []))
    out = {"language": "en", "text": text.strip(), "words": words,
           "asr_model": args.asr, "align_model": args.align}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    progress("done", f"得到 {len(words)} 个词级时间戳")


def main(argv=None):
    p = argparse.ArgumentParser(description="WhisperX 听力切段 worker")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("bootstrap", "transcribe"):
        s = sub.add_parser(name)
        s.add_argument("--asr", default="small.en")
        s.add_argument("--align", default="WAV2VEC2_ASR_BASE_960H")
        s.add_argument("--device", default="cpu")
        s.add_argument("--compute-type", default="int8")
        s.add_argument("--vad-method", default="silero")
        s.add_argument("--model-dir", required=True)
        if name == "transcribe":
            s.add_argument("--audio", required=True)
            s.add_argument("--out", required=True)
            s.add_argument("--batch-size", type=int, default=4)
    args = p.parse_args(argv)
    Path(args.model_dir).mkdir(parents=True, exist_ok=True)
    if args.command == "bootstrap":
        cmd_bootstrap(args)
    else:
        cmd_transcribe(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
