# -*- coding: utf-8 -*-
"""只读最终批次标签体检；与审核台共用目录和规则，不生成或修改标签。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from workbench import taxonomy

RULE_FILES = (taxonomy.FRAMEWORK_PATH, taxonomy.KNOWLEDGE_PATH,
              taxonomy.ROOT / "references" / "type-knowledge-map.json")


def inspect_batch(payload) -> dict:
    errors = []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        errors.append({"code": "BATCH_ITEMS_INVALID", "message": "batch.items 必须是非空数组"})
        items = []
    for index, item in enumerate(items):
        uid = str(item.get("id") or item.get("unit_id") or index) if isinstance(item, dict) else str(index)
        if not isinstance(item, dict):
            problems = ["题目必须是对象"]
        else:
            problems = taxonomy.check_question_labels(item)
        errors.extend({"code": "LABEL_RULE_VIOLATION", "unit_id": uid,
                       "item_index": index, "message": text} for text in problems)
    return {"passed": not errors, "checked_units": len(items), "errors": errors,
            "rule": "fixed-catalog-labels-v1",
            "catalog_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in RULE_FILES}}


def inspect_file(path: Path) -> dict:
    try:
        data = Path(path).read_bytes()
        result = inspect_batch(json.loads(data.decode("utf-8-sig")))
        result["batch_sha256"] = hashlib.sha256(data).hexdigest()
        return result
    except (OSError, ValueError, TypeError) as exc:
        return {"passed": False, "checked_units": 0,
                "errors": [{"code": "LABEL_BATCH_UNREADABLE", "message": str(exc)}]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_file(args.batch)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
