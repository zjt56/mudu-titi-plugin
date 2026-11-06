#!/usr/bin/env python3
"""把教材目录树 Markdown 转成结构化 JSON，供课时内容匹配使用。

只读取 `references/textbook-catalog-1.0.md` 的「完整教材目录树」一节；树状图与
来源表不参与解析。输出保留原始名称与层级，不推断、不改写教材单元名。
用法：python3 scripts/02-structure/build_textbook_catalog.py [--check]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "references" / "textbook-catalog-1.0.md"
OUTPUT = ROOT / "references" / "textbook-catalog.json"
EXPECTED = {"versions": 3, "books": 18, "units": 138}

LINE = re.compile(r"^( *)- (.*?)\s*$")
TOPIC = re.compile(r"^(.*?)（(.*)）$")
UNIT = re.compile(r"^(?:Starter|Unit|Welcome\s+Unit)\b", re.I)
PROJECT = re.compile(r"^Project\b", re.I)


def _split_topic(name: str):
    match = TOPIC.match(name.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return name.strip(), ""


def _short_version(label: str) -> str:
    name = label.strip()
    if name.startswith("牛津译林版"):
        return "译林版"
    return re.sub(r"（.*?）$", "", name).strip()


def _grade_of(book: str) -> str:
    match = re.match(r".*?([七八九]年级)", book)
    return match.group(1) if match else ""


def parse(text: str):
    lines = text.splitlines()
    try:
        start = lines.index("## 完整教材目录树") + 1
    except ValueError as exc:
        raise ValueError("缺少「## 完整教材目录树」小节") from exc
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    versions = []
    for line in lines[start:end]:
        match = LINE.match(line)
        if not match:
            continue
        depth = len(match.group(1)) // 2
        name = match.group(2).strip()
        if not name:
            continue
        if depth == 0:
            versions.append({"label": name, "name": _short_version(name), "grades": []})
            continue
        if not versions:
            raise ValueError(f"目录树层级缺少教材版本：{name}")
        version = versions[-1]
        if depth == 1:
            version["grades"].append({"name": name, "books": []})
            continue
        if not version["grades"]:
            raise ValueError(f"目录树层级缺少年级：{name}")
        grade = version["grades"][-1]
        if depth == 2:
            grade["books"].append({"name": name, "units": []})
            continue
        if not grade["books"]:
            raise ValueError(f"目录树层级缺少册次：{name}")
        book = grade["books"][-1]
        if depth == 3:
            if not (UNIT.match(name) or PROJECT.match(name)):
                # Project 1/2 与单元并列，按远端 chapter 口径作为独立单元保留。
                book.setdefault("extras", []).append(name)
                continue
            unit_name, topic = _split_topic(name)
            book["units"].append({"name": unit_name, "topic": topic, "sections": []})
            continue
        if not book["units"]:
            raise ValueError(f"目录树层级缺少单元：{name}")
        unit = book["units"][-1]
        if depth == 4:
            unit["sections"].append({"name": name, "items": []})
            continue
        if not unit["sections"]:
            raise ValueError(f"目录树层级缺少 Section：{name}")
        unit["sections"][-1]["items"].append(name)

    books = [(v, g, b) for v in versions for g in v["grades"] for b in g["books"]]
    out = {"version": "1.0", "source": SOURCE.name,
           "counts": {"versions": len(versions), "books": len(books),
                      "units": sum(len(b["units"]) for _, _, b in books)},
           "catalog": []}
    for version, grade, book in books:
        entry = {"version": version["name"], "version_label": version["label"],
                 "grade": grade["name"], "book": book["name"],
                 "book_path": f'{version["name"]}/{book["name"]}',
                 "units": [{"unit": unit["name"], "topic": unit["topic"],
                            "path": f'{version["name"]}/{book["name"]}/{unit["name"]}',
                            "sections": unit["sections"]} for unit in book["units"]]}
        if book.get("extras"):
            entry["extras"] = list(book["extras"])
        out["catalog"].append(entry)
    if out["counts"] != EXPECTED:
        raise ValueError(f"目录数量异常：{out['counts']}，期望 {EXPECTED}")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)
    data = parse(SOURCE.read_text(encoding="utf-8-sig"))
    if not args.check:
        OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已生成 {OUTPUT.relative_to(ROOT)}：{data['counts']}")
    else:
        current = json.loads(OUTPUT.read_text(encoding="utf-8-sig")) if OUTPUT.is_file() else None
        if current != data:
            print("textbook-catalog.json 与 Markdown 不一致，请重新生成", file=sys.stderr)
            return 1
        print(f"一致：{data['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
