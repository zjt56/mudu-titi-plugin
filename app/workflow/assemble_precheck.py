# -*- coding: utf-8 -*-
"""装配产物的格式预检：导入前先跑体检脚本，避免到 validate 才发现写法问题。

只读、离线：把候选批次写到临时文件，依次运行文本/空档/表格/结构/题型版面
五项确定性检查。返回的问题会直接给回执行者（含一次带反馈的重试），
不修改候选、也不代表审核或放行。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "check_text.py",
    "audit_underlines.py",
    "check_tables.py",
    "check_structure.py",
    "audit_type_layout.py",
)


def precheck(payload: dict, timeout: int = 120) -> dict:
    """Return {"passed": bool, "problems": [str]} for an assemble payload."""
    if not isinstance(payload, dict):
        return {"passed": False, "problems": ["装配结果必须是对象"]}
    with tempfile.TemporaryDirectory(prefix="assemble-precheck-") as tmp:
        path = Path(tmp) / "batch.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        problems: list[str] = []
        for name in SCRIPTS:
            script = ROOT / "scripts" / "06-upload" / name
            try:
                proc = subprocess.run(
                    [sys.executable, str(script), "--batch", str(path)],
                    cwd=str(ROOT), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                problems.append(f"{name}: 预检无法执行（{exc}）")
                continue
            if proc.returncode == 0:
                continue
            lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
            lines = [line for line in lines if not line.startswith("[FAIL]")]
            problems.extend(lines[:20] or [f"{name}: 预检失败（exit {proc.returncode}）"])
        return {"passed": not problems, "problems": problems}
