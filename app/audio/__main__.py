# -*- coding: utf-8 -*-
"""命令行入口：python -m audio <status|config|bootstrap|segment>。

由工作台后端在后台线程里调用；也可手工排查。全部读同一份 config/audio.json
与数据根 status.json，和设置页看到的状态一致。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import bootstrap, config as cfgmod, segment


def main(argv=None):
    p = argparse.ArgumentParser(description="英语听力切段适配器（可选能力）")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("config")
    b = sub.add_parser("bootstrap")
    b.add_argument("--force", action="store_true")
    s = sub.add_parser("segment")
    s.add_argument("--batch-dir", required=True)
    s.add_argument("--audio", default=None)
    args = p.parse_args(argv)

    if args.command == "config":
        cfg = cfgmod.load()
        data_dir = Path(cfgmod.data_dir(cfg))
        print(json.dumps({"config": cfg, "config_path": str(cfgmod.config_path()),
                          "data_dir": str(data_dir), "venv_python": str(bootstrap.venv_python(cfg)),
                          "ready": bootstrap.ready(cfg)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        cfg = cfgmod.load()
        state = bootstrap.read_status(Path(cfgmod.data_dir(cfg)))
        state.update({"ready": bootstrap.ready(cfg), "config": cfg,
                      "data_dir": str(cfgmod.data_dir(cfg))})
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if state.get("ok") is not False else 1
    if args.command == "bootstrap":
        cfg = cfgmod.load()
        print(json.dumps(bootstrap.bootstrap(cfg, force=args.force), ensure_ascii=False, indent=2))
        return 0
    if args.command == "segment":
        cfg = cfgmod.load()
        data_dir = Path(cfgmod.data_dir(cfg))
        def progress(phase, pct, detail):
            bootstrap.write_status(data_dir, job="segment", running=True, ok=None,
                                   phase=phase, pct=pct, detail=detail, error="")
        try:
            result = segment.run(Path(args.batch_dir), cfg, args.audio, progress)
        except Exception as exc:  # noqa: BLE001 - 后台任务需把失败写进状态
            bootstrap.write_status(data_dir, job="segment", running=False, ok=False,
                                   phase="error", detail="", error=str(exc))
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
        bootstrap.write_status(data_dir, job="segment", running=False, ok=True,
                               phase="done", pct=100, detail=f"已生成 {result['units']} 段", error="")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
