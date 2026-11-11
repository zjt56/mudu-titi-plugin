"""教材目录（课时同步练习）查询与校验。

目录来源：`references/textbook-catalog-1.0.md` → `references/textbook-catalog.json`。
只做精确匹配：版本、册次、单元、Section 名称必须与目录完全一致，不做模糊猜测。
试卷不要求教材匹配；只有课时同步练习类来源才使用本目录。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "references" / "textbook-catalog.json"
SOURCE_PATH = ROOT / "references" / "textbook-catalog-1.0.md"
SYNC_SOURCES = frozenset(("同步练习", "单元测验"))
_cache = {"mtime": None, "data": None}


def catalog() -> dict:
    mtime = CATALOG_PATH.stat().st_mtime
    if _cache["mtime"] != mtime or _cache["data"] is None:
        _cache["data"] = json.loads(CATALOG_PATH.read_text(encoding="utf-8-sig"))
        _cache["mtime"] = mtime
    return _cache["data"]


def versions() -> list:
    return sorted({entry["version"] for entry in catalog()["catalog"]})


def books(version: str | None = None) -> list:
    return [entry for entry in catalog()["catalog"] if version is None or entry["version"] == version]


def book(name: str) -> dict:
    for entry in catalog()["catalog"]:
        if entry["book"] == name:
            return entry
    raise ValueError(f"教材目录中没有该册次：{name}")


def units(version: str | None = None, book_name: str | None = None) -> list:
    rows = []
    for entry in books(version):
        if book_name and entry["book"] != book_name:
            continue
        rows.extend(dict(unit, version=entry["version"], book=entry["book"], grade=entry["grade"])
                    for unit in entry["units"])
    return rows


def resolve(version: str, book_name: str, unit_name: str, section: str | None = None) -> dict:
    """按目录精确解析教材位置；任一层不一致即拒绝，不回退到相似名称。"""
    entry = book(book_name)
    if entry["version"] != version:
        raise ValueError(f"册次 {book_name} 属于 {entry['version']}，不是 {version}")
    match = next((unit for unit in entry["units"] if unit["unit"] == unit_name), None)
    if match is None:
        raise ValueError(f"{book_name} 中没有该单元：{unit_name}")
    placement = {"version": version, "book": book_name, "grade": entry["grade"],
                 "unit": unit_name, "topic": match.get("topic", "")}
    if section:
        if not any(item["name"] == section for item in match["sections"]):
            raise ValueError(f"{book_name}/{unit_name} 中没有该 Section：{section}")
        placement["section"] = section
    return placement


def validate_placement(value) -> dict:
    """独立教材候选校验工具；自动知识点流程当前不调用。"""
    if not isinstance(value, dict):
        raise ValueError("textbook placement must be an object")
    # resolve() includes catalog-derived grade; accept our normalized output
    # again, but never trust a caller-supplied grade over the exact catalog.
    allowed = {"version", "book", "unit", "section", "topic", "grade", "evidence"}
    if set(value) - allowed:
        raise ValueError(f"textbook placement has unknown fields: {sorted(set(value) - allowed)}")
    for key in ("version", "book", "unit"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"textbook placement requires a nonempty {key}")
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence or any(
            not isinstance(x, str) or not x.strip() for x in evidence):
        raise ValueError("textbook placement requires nonempty evidence")
    placement = resolve(value["version"], value["book"], value["unit"], value.get("section"))
    if "grade" in value and value["grade"] != placement["grade"]:
        raise ValueError("textbook placement grade differs from the catalog")
    if value.get("topic") and value["topic"] != placement["topic"]:
        raise ValueError("textbook placement topic differs from the catalog")
    placement["evidence"] = list(evidence)
    return placement


def compact_index(include_sections: bool = False) -> dict:
    """给模型的最小目录：版本 → 册次 → 单元（可选 Section）。"""
    out = []
    for entry in catalog()["catalog"]:
        row = {"version": entry["version"], "book": entry["book"], "grade": entry["grade"], "units": []}
        for unit in entry["units"]:
            item = {"unit": unit["unit"], "topic": unit.get("topic", "")}
            if include_sections:
                item["sections"] = [section["name"] for section in unit["sections"]]
            row["units"].append(item)
        out.append(row)
    return {"versions": versions(), "books": out,
            "note": "只用于课时同步练习来源；名称必须逐字一致，不得改写或猜测。"}


def needs_matching(source_type) -> bool:
    return isinstance(source_type, str) and source_type in SYNC_SOURCES


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="教材目录查询：versions / books / units / index")
    sub = parser.add_subparsers(dest="command", required=True)
    p_unit = sub.add_parser("units", help="列出单元")
    p_unit.add_argument("--version")
    p_unit.add_argument("--book")
    p_resolve = sub.add_parser("resolve", help="精确解析一个教材位置")
    p_resolve.add_argument("--version", required=True)
    p_resolve.add_argument("--book", required=True)
    p_resolve.add_argument("--unit", required=True)
    p_resolve.add_argument("--section")
    sub.add_parser("index", help="输出紧凑目录索引")
    args = parser.parse_args(argv)
    if args.command == "units":
        print(json.dumps(units(args.version, args.book), ensure_ascii=False, indent=2))
    elif args.command == "resolve":
        print(json.dumps(resolve(args.version, args.book, args.unit, args.section), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(compact_index(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
