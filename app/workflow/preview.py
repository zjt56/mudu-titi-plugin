# -*- coding: utf-8 -*-
"""生成本地预览页：模板 + 数据 → 一页 HTML，双击就能翻整套卷。

拆分方式：
- workflow/templates/preview.html 是模板，里面只有 __TITLE__ 与 __DATA__ 两个占位；
- 本模块负责收集数据、替换占位、覆盖写出 <批次>/preview.html。

所以改样式或交互只改模板，改数据字段只改这里，互不影响。
每次生成都覆盖同一个 preview.html，浏览器刷新即可看到最新状态。

数据来源与通报一致（report.collect），避免"通报说通过、页面说有问题"。
题目正文按题号从 01_source/正文.txt 机械切片，只用于快速预览，
不是结构确认的结果，不能替代原卷核对。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import manifest as mf, report
from .extract import _plain, guide_numbers, material_label, material_start
from .denoise import NUMBERED_TITLE

OUT = "preview.html"
TEMPLATE = Path(__file__).resolve().parent / "templates" / "preview.html"
MAX_LINES = 12

START_PATTERNS = (
    re.compile(r"[（(]\s*[）)]\s*(\d{1,3})\s*[.．]"),
    re.compile(r"^\s*(\d{1,3})\s*[.．]"),
    re.compile(r"\u3000\s*(\d{1,3})(?!\d)"),
)
SECTION = re.compile(r"^\s*(第[一二三四五六七八九十]+(?:部分|节)|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+[.．、]|[一二三四五六七八九十]+[、．.])")
IMG = re.compile(r"\[IMG\s+([^\]]+)\]")
# Only a complete numbered exam title is a header; a year can begin a real question.
TITLE_LIKE = NUMBERED_TITLE
# 题组导语（「听下面一段对话，回答第8至10题。」）属于下一组，
# 不属于上一题的题干，切片时必须在这里截断。
LEADIN = re.compile(
    r"^\s*(听下面|听一段|听两段|阅读下列|阅读下面|阅读短文|根据短文|根据材料|"
    r"你将听到|请根据|完成下面|回答第|从每小题所给|从方框内)")
# 选项行（A.xxx B.xxx）属于上一题，表格向上回溯时不能带进来
OPTION_LINE = re.compile(r"^\s*A\s*[.．].*?B\s*[.．]")
# 单独成行的选项（A.About 15 minutes.）属于上一题，回溯材料时不能带进来
BARE_OPTION = re.compile(r"^\s*[A-H]\s*[.．、]\s*\S")


def _short_knowledge(path: str) -> str:
    """知识点标签只显示二级与最后一级（最多两个）。"""
    parts = [p for p in str(path or "").split("/") if p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[1]
    return "/".join([parts[1], parts[-1]])


def _short_knowledge_list(values) -> list[str]:
    return [_short_knowledge(p) for p in (values or []) if str(p).strip()]


def _strong_number(line: str) -> int | None:
    """真正的题号行：(　)31. 或 31. 开头。"""
    for pattern in START_PATTERNS[:2]:
        match = pattern.search(_plain(line))
        if match:
            return int(match.group(1))
    return None


def _weak_numbers(line: str) -> list[int]:
    """材料一行里可能有多个空位（31–40 同段），必须全部扫出来。"""
    return [int(m.group(1)) for m in re.finditer(r"(?:\u3000|__|_{1,})\s*(\d{1,3})(?!\d)", _plain(line))]


def _weak_number(line: str) -> int | None:
    found = _weak_numbers(line)
    return found[0] if found else None


def _start_number(line: str) -> int | None:
    return _strong_number(line) or _weak_number(line)


BLANK = re.compile(r"\u3000\s*{}(?!\d)")


def _sentence_with(text: str, number: int) -> str:
    """从材料段落里取出包含该空位的那一句。"""
    pattern = re.compile(BLANK.pattern.format(number))
    for part in re.split(r"(?<=[.?!。？！])\s*", text):
        if pattern.search(part):
            return part.strip()
    return text.strip()[:120]


def _find(lines: list[str], number: int, strong: bool = True) -> int | None:
    for index, line in enumerate(lines):
        if TITLE_LIKE.fullmatch(_plain(line).strip()):
            continue
        if strong:
            if _strong_number(line) == number:
                return index
        elif number in _weak_numbers(line):
            return index
    return None


def _table_block(lines: list[str], index: int) -> list[str]:
    """空位在表格里时，返回整张表 + 上方作答说明。

    信息转换（16-20）的题干就是那张表，只切一行表格碎片是看不懂的。
    """
    start = None
    for cursor in range(index, -1, -1):
        text = lines[cursor].strip()
        if text.startswith("[/TABLE]"):
            return []
        if text.startswith("[TABLE"):
            start = cursor
            break
    if start is None:
        return []
    end = None
    for cursor in range(start, len(lines)):
        if lines[cursor].strip().startswith("[/TABLE]"):
            end = cursor
            break
    if end is None:
        return []

    top = start
    cursor = start - 1
    while cursor >= 0 and start - cursor <= 3:
        text = lines[cursor].strip()
        if not text:
            cursor -= 1
            continue
        if _start_number(text) is not None:
            break
        if OPTION_LINE.match(text):
            break
        # 大题标题行（如「Ⅳ.信息转换(共5小题…)」「Ⅹ.书面表达…」）不属于题目切片
        if SECTION.match(lines[cursor]):
            break
        top = cursor
        cursor -= 1
    return [line.rstrip() for line in lines[top:end + 1] if line.strip()]


def slice_text(lines: list[str], number: int) -> list[str]:
    """从原文里切出某题的片段，用于预览。

    - 空位在表格里（信息转换）：返回整张表 + 作答说明；
    - 优先取真正的题号行（`(　)31.` 这种）；完形选项单独成行、空位埋在材料里，
      所以再把含该空的那一句放到最前面。
    """
    start = _find(lines, number, strong=True)
    weak = _find(lines, number, strong=False)
    chunk: list[str] = []

    anchor = start if start is not None else weak
    if anchor is not None:
        block = _table_block(lines, anchor)
        if block:
            return block

    if start is not None:
        if weak is not None and weak < start:
            sentence = _sentence_with(lines[weak], number)
            if sentence:
                chunk.append("材料：" + sentence)
    else:
        start = weak
    if start is None:
        # 无印刷题号的作文只允许通过结构里的明确大题范围绑定；
        # 纯题号切片不能把任何未来编号映射到最后一段。
        return []

    chunk.append(lines[start])
    for line in lines[start + 1:]:
        if SECTION.match(_plain(line)) or LEADIN.match(_plain(line)) or material_label(line):
            break
        found = _start_number(line)
        if found is not None and found != number:
            break
        chunk.append(line)
    return [line.rstrip() for line in chunk if line.strip()]


def slice_range(lines: list[str], numbers) -> list[str]:
    """切出一组小题（题组：完形、任务型阅读等）的完整原文片段。

    与 slice_text 的区别：不因遇到组内下一个题号而截断，一直取到组末小题之后的
    下一节/下一组导语/更大的题号为止，因此共享材料只出现一次、小题连续排列。
    """
    nums = []
    for value in numbers or []:
        text = str(value).strip()
        if text.isdigit():
            nums.append(int(text))
    if not nums:
        return []
    first, last = min(nums), max(nums)
    start = _find(lines, first, strong=True)
    weak = _find(lines, first, strong=False)
    if start is None:
        start = weak
    elif weak is not None and weak < start:
        start = weak
    if start is None:
        return []

    # 从首题向上补回共享材料；明确材料标题/分段导语是本组的起点。
    top = start
    for cursor in range(start - 1, -1, -1):
        text = _plain(lines[cursor]).strip()
        if SECTION.match(text) or _strong_number(text) is not None or BARE_OPTION.match(text):
            break
        if _weak_numbers(text) and not set(_weak_numbers(text)).issubset(nums):
            break
        top = cursor
        if material_label(text):
            top = material_start(lines, cursor)
            break
        if guide_numbers(text):
            break

    chunk = []
    for cursor in range(top, len(lines)):
        line = lines[cursor]
        plain = _plain(line).strip()
        if cursor > top and (SECTION.match(plain) or (cursor > start and material_label(plain))):
            break
        found = _strong_number(line)
        if found is not None and found not in nums:
            break
        refs = guide_numbers(line)
        if refs and not set(refs).issubset(nums):
            break
        if cursor > start and LEADIN.match(plain) and not refs:
            break
        chunk.append(line)
    # 空行不是选项块结束的证据；带括号/画线/粗体的末题和 A.–D. 均完整保留。
    return [line.rstrip() for line in chunk if line.strip() and not material_label(line)]


def source_slice(batch_dir, numbers, lines: list[str] | None = None) -> list[str]:
    """Preview source by numbers, using explicit section binding for unnumbered writing.

    Rebuild the binding from the current source/structure rather than trusting a
    stale confirmed stem. This displays candidates without approving numbering.
    """
    root = Path(batch_dir)
    if lines is None:
        path = root / "01_source/正文.txt"
        lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    nums = [int(n) for n in numbers if str(n).isdigit()]
    if not nums:
        return []
    chunk = slice_range(lines, nums) if len(nums) > 1 else slice_text(lines, nums[0])
    if chunk or len(nums) != 1:
        return chunk
    try:
        from . import denoise, structure_gate
        structure = json.loads((root / "01_source/structure.json").read_text(encoding="utf-8-sig"))
        cleaned, meta, _ = denoise.clean_body("\n".join(lines))
        units = structure_gate._build_units(structure.get("sections") or [], structure.get("groups") or [], cleaned.splitlines())
        matches = [u for u in units if u.get("numbers") == nums
                   and u.get("type") == "书面表达"
                   and u.get("source_span", {}).get("number_binding") == "single_writing_section"]
        if len(matches) != 1:
            return []
        titles = {_plain(str(meta.get(key) or "")).strip() for key in ("source", "paper_title")}
        body = []
        for line in matches[0]["stem"].splitlines():
            text = _plain(line).strip()
            if text and (text in titles or re.match(r"^(?:参考答案|答案与解析|附录)(?:\s|[:：]|$)", text)):
                break
            if line.strip():
                body.append(line.rstrip())
        return body
    except (OSError, ValueError, TypeError, KeyError):
        return []


def batch_rows(batch_dir, review_rows: list[dict]) -> list[dict] | None:
    """按入库单元合并后的行：一个独立题一行，一个题组一行（带 numbers/subquestions）。"""
    return _rows_from_batch(Path(batch_dir), review_rows)


def _media_refs(chunk: list[str]) -> list[str]:
    refs = []
    for line in chunk:
        for name in IMG.findall(line):
            name = name.strip()
            if name not in refs:
                refs.append(name)
    return refs


def parse_table(text: str) -> dict | None:
    """把切片里的 [TABLE …] 块解析成行列，供页面渲染成真正的表格。"""
    if "[TABLE" not in text:
        return None
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("[TABLE")), None)
    end = next((i for i, l in enumerate(lines) if l.strip().startswith("[/TABLE]")), None)
    if start is None or end is None or end <= start:
        return None
    rows = []
    for line in lines[start + 1:end]:
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split("|")]
        rows.append(cells)
    if not rows:
        return None
    return {
        "before": "\n".join(lines[:start]).strip(),
        "rows": rows,
        "after": "\n".join(lines[end + 1:]).strip(),
    }


SEVERITY_ORDER = {"blocked": 4, "attention": 3, "unverified": 2, "ok": 1}


def _numbers_of(item: dict) -> list[int]:
    subs = item.get("subquestions") or []
    nums = [int(s["number"]) for s in subs if str(s.get("number", "")).isdigit()]
    if nums:
        return nums
    # 独立题优先用显式 numbers / number（中考真题按小题拆，题号即 1..N）。
    explicit = item.get("numbers")
    if isinstance(explicit, list) and explicit:
        got = [int(n) for n in explicit if str(n).isdigit()]
        if got:
            return got
    if str(item.get("number", "")).isdigit():
        return [int(item["number"])]
    # 练习册兼容：id 段位末 3 位。
    match = re.search(r"(\d{3})$", str(item.get("id", "")))
    return [int(match.group(1))] if match else []


def _worst(states: list[str]) -> str:
    if not states:
        return "unverified"
    return max(states, key=lambda s: SEVERITY_ORDER.get(s, 0))


def _blocks_of(item: dict) -> list | None:
    """取表格块：优先用 materialBlocks，没有就按竖线机械还原。"""
    blocks = item.get("materialBlocks") or item.get("material_blocks")
    if isinstance(blocks, list) and blocks:
        return blocks
    material = item.get("material") or ""
    lines = [ln for ln in material.splitlines() if ln.strip()]
    if len(lines) >= 2 and all("|" in ln for ln in lines):
        return [{"type": "table", "rows": [
            {"cells": [{"text": part.strip()} for part in line.split("|")]}
            for line in lines
        ]}]
    return None


def source_group_candidates(batch_dir: Path, items: list[dict]) -> list[dict]:
    """Project obsolete merged groups into current source groups for review only.

    Formal batch files stay invalidated until reassembled. Old audio and review
    verdicts are not evidence for the new units.
    """
    try:
        from . import denoise, structure_gate
        from .question_content import question_parts
        root = Path(batch_dir)
        raw = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
        structure = json.loads((root / "01_source/structure.json").read_text(encoding="utf-8-sig"))
        cleaned, _, _ = denoise.clean_body(raw)
        units = structure_gate._build_units(structure.get("sections") or [], structure.get("groups") or [], cleaned.splitlines())
    except (OSError, ValueError, TypeError, KeyError):
        return items
    result = []
    for item in items:
        nums = _numbers_of(item)
        pieces = [u for u in units if u.get("unit_kind") == "group" and set(u["numbers"]).issubset(nums)
                  and u.get("type") == (item.get("question_type") or item.get("type"))]
        covered = [n for u in pieces for n in u["numbers"]]
        if item.get("unit_kind") != "group" or len(pieces) < 2 or sorted(covered) != sorted(nums):
            result.append(item)
            continue
        old_children = {str(c.get("number")): c for c in item.get("subquestions") or []}
        projected = []
        for unit in pieces:
            numbers = unit["numbers"]
            children = []
            for n in numbers:
                part = question_parts(unit["stem"].splitlines(), n)
                if not part["found"]:
                    break
                child = dict(old_children.get(str(n), {}), number=n, stem=part["stem"], options=part["options"])
                for key in ("audio", "audioFile", "audio_file", "audio_url", "audioUrl"):
                    child.pop(key, None)
                children.append(child)
            if len(children) != len(numbers):
                # Do not hide an old row if independent reconstruction failed.
                projected = []
                break
            filters = dict(item.get("filterMetadata") or {})
            if unit["type"] == "听力长对话理解":
                filters["subtypes"] = [f"选择{len(numbers)}题"]
            uid = "group-" + "-".join(map(str, numbers))
            projected.append({"id": uid, "unit_id": uid, "unit_kind": "group", "number": numbers[0],
                           "numbers": numbers, "question_type": unit["type"], "filterMetadata": filters,
                           "stem": unit["stem"], "material": "", "options": [], "subquestions": children,
                           "knowledge": _short_knowledge_list(item.get("knowledge")), "source_projection": True,
                           "source_parent_id": item.get("id") or item.get("unit_id"),
                           "source_notice": "按原卷重新分组的待审候选；正式批次、知识点及分段音频需重做核验。"})
        result.extend(projected or [item])
    return result


def _rows_from_batch(batch_dir: Path, review_rows: list[dict]) -> list[dict] | None:
    """有正式批次时，按题库字段（material / stem / options / subquestions）出卡片。"""
    path = batch_dir / "06_batch" / "batch.json"
    if not path.is_file():
        return None
    try:
        batch = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
    items = batch.get("items") or []
    if not items:
        return None
    items = source_group_candidates(batch_dir, items)

    by_number = {str(r["number"]): r for r in review_rows}
    rows = []
    for item in items:
        nums = _numbers_of(item)
        if not nums:
            continue
        related = [by_number[str(n)] for n in nums if str(n) in by_number]
        state = "unverified" if item.get("source_projection") else _worst([r["state"] for r in related])
        opened, resolved = [], []
        for r in related:
            opened.extend(r.get("open") or [])
            resolved.extend(r.get("resolved") or [])
        label = str(nums[0]) if len(nums) == 1 else f"{nums[0]}–{nums[-1]}"
        rows.append({
            "unit_id": item.get("id", ""),
            "number": str(nums[0]),
            "label": label,
            "numbers": nums,
            "unit_kind": item.get("unit_kind", "standalone"),
            "type": item.get("type") or item.get("question_type", ""),
            "answer": item.get("answer", ""),
            "difficulty": item.get("difficulty", ""),
            "knowledge": _short_knowledge_list(item.get("knowledge")),
            "state": state,
            "rounds": 0 if item.get("source_projection") else max([r.get("rounds") or 0 for r in related] or [0]),
            "verdict": "missing" if item.get("source_projection") else next((r.get("verdict") for r in related if r.get("verdict")), "missing"),
            "source_projection": bool(item.get("source_projection")),
            "source_parent_id": item.get("source_parent_id"),
            "source_notice": item.get("source_notice", ""),
            "open": opened,
            "resolved": resolved,
            "material": item.get("material") or "",
            "blocks": _blocks_of(item),
            "stem": item.get("stem") or "",
            "options": item.get("options") or [],
            "subquestions": item.get("subquestions") or [],
            "text": "",
            "table": None,
            "media": [],
        })
    rows.sort(key=lambda r: int(r["number"]) if r["number"].isdigit() else 10 ** 6)
    return rows


def build_data(batch_dir) -> dict:
    batch_dir = Path(batch_dir)
    data = report.collect(batch_dir)
    try:
        manifest = mf.load(batch_dir)
    except FileNotFoundError:
        manifest = {}

    text_path = batch_dir / "01_source" / "正文.txt"
    lines = text_path.read_text(encoding="utf-8").splitlines() if text_path.is_file() else []

    rows = _rows_from_batch(batch_dir, data["rows"])
    mode = "batch" if rows else "slice"

    if rows is None:
        rows = []
        for row in data["rows"]:
            chunk = source_slice(batch_dir, [int(row["number"])], lines) if row["number"].isdigit() else []
            text = "\n".join(chunk)
            rows.append({
                "unit_id": row["unit_id"],
                "number": row["number"],
                "label": row["number"],
                "type": row["type"],
                "answer": row["answer"],
                "difficulty": row["difficulty"],
                "knowledge": _short_knowledge_list(row.get("knowledge")),
                "state": row["state"],
                "rounds": row["rounds"],
                "verdict": row["verdict"],
                "open": row["open"],
                "resolved": row["resolved"],
                "text": text,
                "table": parse_table(text),
                "media": _media_refs(chunk),
            })

    return {
        "batch": data["batch"],
        "batch_dir": str(batch_dir),
        "mode": mode,
        "generated_at": data.get("generated_at") or mf.now_iso(),
        "meta": manifest.get("meta", {}),
        "status": manifest.get("status"),
        "total": len(rows) if mode == "batch" else data["total"],
        "counts": {k: sum(1 for r in rows if r["state"] == k) for k in
                   ("ok", "attention", "blocked", "unverified")},
        "coverage": data.get("coverage", {}),
        "gate": data["gate"],
        "validation": data["validation"],
        "rows": rows,
    }


def load_template() -> str:
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"找不到预览模板：{TEMPLATE}")
    return TEMPLATE.read_text(encoding="utf-8")


def render_html(data: dict, template: str | None = None) -> str:
    tpl = template if template is not None else load_template()
    for token in ("__TITLE__", "__DATA__"):
        if token not in tpl:
            raise ValueError(f"模板缺少占位符 {token}：{TEMPLATE}")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"{data.get('batch', '批次')} 预览"
    html_text = tpl.replace("__TITLE__", title).replace("__DATA__", payload)
    if "__DATA__" in html_text or "__TITLE__" in html_text:
        raise ValueError("占位符未被完全替换，检查模板里是否重复出现")
    return html_text


def write(batch_dir) -> Path:
    batch_dir = Path(batch_dir)
    data = build_data(batch_dir)
    target = batch_dir / OUT
    target.write_text(render_html(data), encoding="utf-8")
    return target


def refresh(batch_dir, quiet: bool = True) -> Path | None:
    """供其它命令调用：生成失败不影响主流程。"""
    try:
        return write(batch_dir)
    except Exception as exc:  # noqa: BLE001 - 预览只是辅助，不能拖垮主流程
        if not quiet:
            print(f"  · 预览生成失败：{exc}")
        return None
