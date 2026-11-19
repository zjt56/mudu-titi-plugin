"""Source-bound provenance for explicit workbench text edits."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

RELATIVE = "workflow/manual-content-edits.json"
VERSION = 1
FIELDS = ("articleSummary", "explanation")


def source_binding(batch_dir):
    from .agent_tasks import _safe_path
    from .upload_content import SOURCES

    root = Path(batch_dir)
    paths = {*SOURCES, "01_source/structure.confirmed.json", "01_source/material-roles.json"}
    paths.update(p.relative_to(root).as_posix() for p in (root / "01_source/materials").glob("attachment_*.txt"))
    return {rel: hashlib.sha256(path.read_bytes()).hexdigest()
            for rel in sorted(paths) if (path := _safe_path(root, rel)).is_file()}


def load(batch_dir):
    from .agent_tasks import _safe_path

    path = _safe_path(batch_dir, RELATIVE)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {"version": VERSION, "edits": {}}
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("edits"), dict):
        raise ValueError("人工内容修改记录格式无效")
    return data


def prepare(batch_dir, unit_id, edit):
    data = load(batch_dir)
    binding = source_binding(batch_dir)
    previous = data["edits"].get(unit_id)
    fields = {key: previous[key] for key in FIELDS if key in previous} if (
        isinstance(previous, dict) and previous.get("source_binding") == binding) else {}
    fields.update({key: edit[key] for key in FIELDS if key in edit})
    data["edits"][unit_id] = {**fields, "source_binding": binding, "updated_by": "workbench",
                              "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    return data
