# -*- coding: utf-8 -*-
"""装配最小结构验收：只查结构与选项完整性，不启动纯格式检查。

空档、画线、表格写法和重复显示留到最终 format_gate；这些问题不得触发模型重试。
只读、离线，不代表审核或上传放行。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "check_structure.py",
    "audit_type_layout.py",
)


def precheck(payload: dict, timeout: int = 120, batch_dir: str | Path | None = None) -> dict:
    """Return {"passed": bool, "problems": [str]} for an assemble payload."""
    if not isinstance(payload, dict):
        return {"passed": False, "problems": ["装配结果必须是对象"]}
    with tempfile.TemporaryDirectory(prefix="assemble-precheck-") as tmp:
        path = Path(tmp) / "batch.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        problems: list[str] = []
        for name in SCRIPTS:
            script = ROOT / "scripts" / "06-upload" / name
            command = [sys.executable, str(script), "--batch", str(path)]
            if name == "audit_type_layout.py":
                command.append("--content-only")
            if name == "check_structure.py" and batch_dir is not None:
                command.extend(["--source-batch-dir", str(Path(batch_dir).resolve())])
            try:
                proc = subprocess.run(
                    command,
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
