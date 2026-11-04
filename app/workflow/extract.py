# -*- coding: utf-8 -*-
"""复合原卷提取：Word 原生读取、PDF 逐页路由、本地视觉 OCR，产出可追溯候选。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow import manifest as mf

VERSION = "composite-extract-1"
QUESTION = re.compile(r"^\s*(?:[（(]\s*[）)]\s*)?(\d{1,4})\s*[.．、)]")
HEADING = re.compile(r"^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+|[一二三四五六七八九十]+|\d+)\s*[.．、]\s*(.+)")
TYPE_HINTS = (
    ("关键词语选择", "听力关键词语选择"), ("短对话理解", "听力短对话理解"),
    ("长对话理解", "听力长对话理解"), ("短文理解", "听力短文理解"),
    ("信息转换", "听力信息转换"), ("教材语篇", "教材语篇填空"),
    ("任务型阅读", "任务型阅读"), ("读写综合", "读写综合"),
    ("完形填空", "完形填空"), ("语法选择", "语法选择"),
    ("阅读理解", "阅读理解"), ("补全对话", "补全对话"),
    ("单项填空", "单项填空"), ("单项选择", "单项填空"),
    ("单词拼写", "单词拼写"), ("词汇运用", "单词拼写"),
    ("完成句子", "完成句子"), ("选词填空", "选词填空"),
    ("短文填空", "短文填空"), ("书面表达", "书面表达"),
    ("情景运用", "情景运用"), ("翻译", "翻译"), ("语音", "语音题"),
)
GROUP_TYPES = {"完形填空", "语法选择", "阅读理解", "任务型阅读", "读写综合", "补全对话",
               "听力长对话理解", "听力短文理解", "听力信息转换", "选词填空", "短文填空", "教材语篇填空"}


def _plain(line):
    return re.sub(r"</?(?:u|sup|sub)>", "", line).replace("**", "")


def blank_numbers(line: str) -> list[int]:
    """Read numbers that sit inside an underline/blank, tolerating NBSP padding.

    Word exports sometimes pad the blank with ``\xa0`` instead of the ideographic
    space, so a blank like ``<u>\u300066\xa0</u>`` must still be recognized.
    """
    normalized = line.replace("\xa0", "\u3000")
    return [int(n) for n in re.findall(
        r"(?:_{2,}|\u3000+)\s*(\d{1,3})\s*(?=_{2,}|\u3000)", normalized)]


def guide_numbers(line: str) -> list[int]:
    """Read only explicit answering ranges/lists in a group lead-in."""
    match = re.search(r"回答\s*第?\s*([\d\s、,，和及至到\-—–～~第]+)\s*(?:小)?题", _plain(line))
    if not match:
        return []
    value = match.group(1)
    nums = [int(n) for n in re.findall(r"\d+", value)]
    if len(nums) == 2 and re.search(r"至|到|[\-—–～~]", value):
        return list(range(nums[0], nums[1] + 1)) if nums[0] <= nums[1] else []
    return nums


def material_label(line: str) -> bool:
    return bool(re.fullmatch(r"[A-H][.．、]?", _plain(line).strip()))


def material_start(lines: list[str], index: int) -> int:
    """A new article's answering instruction may precede its A/B/F label."""
    cursor = index - 1
    while cursor >= 0 and not lines[cursor].strip():
        cursor -= 1
    if cursor >= 0 and re.match(r"^\s*(?:阅读下列|阅读下面|根据短文|根据材料|根据对话|从方框内)", _plain(lines[cursor])):
        return cursor
    return index


def source_group_partitions(lines: list[str], numbers: list[int]) -> list[dict]:
    """Locate complete source-backed partitions, even for legacy structures."""
    wanted = set(numbers)
    boundaries = [material_start(lines, i) if material_label(line) else i
                  for i, line in enumerate(lines) if material_label(line) or guide_numbers(line)]
    parts = []
    for offset, start in enumerate(boundaries):
        end = boundaries[offset + 1] if offset + 1 < len(boundaries) else len(lines)
        for cursor in range(start + 1, end):
            heading = HEADING.match(_plain(lines[cursor]).strip())
            if heading and any(hint in heading.group(2) for hint, _ in TYPE_HINTS):
                end = cursor
                break
        found = set()
        for line in lines[start + 1:end]:
            plain = _plain(line)
            match = QUESTION.match(plain)
            if match:
                found.add(int(match.group(1)))
            found.update(blank_numbers(plain))
        members = sorted(found & wanted)
        refs = guide_numbers(lines[start])
        if members and found.issubset(wanted) and (not refs or set(refs) == set(members)):
            parts.append({"questions": members, "source_start_line": start + 1,
                          "source_end_line": end, "boundary_method": "group_leadin" if refs else "material_heading"})
    flattened = [n for part in parts for n in part["questions"]]
    return parts if set(flattened) == wanted and len(flattened) == len(wanted) else []


def looks_like_answers(lines: list[str]) -> bool:
    """Duplicate headline tail is an answer key when it opens with number+letter rows or 解析."""
    head = [(_plain(line)).strip() for line in lines[:30] if line.strip()]
    if not head:
        return False
    answer_row = re.compile(r"^\s*\d+\s*[~～\-–—]?\s*\d*\s*[.:：]?\s*[A-H]{2,}\b")
    explain = re.compile(r"【解析】|【答案】|【分析】|参考答案|答案与解析")
    hits = sum(1 for line in head if answer_row.search(line) or explain.search(line))
    return hits >= 2


def _title_key(line: str) -> str:
    plain = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", _plain(line)))
    # 卷名行可能带列表前缀（如 **1**.**2017**年…），那不是题号，也不能让文末重复卷名漏删。
    return re.sub(r"^\d{1,3}[.．、]", "", plain)


def _answer_summary_follows(lines: list[str]) -> bool:
    """Require an adjacent question-number/answer table, not later answer-like prose."""
    rows = iter(line.strip() for line in lines if line.strip())
    first = next(rows, "")
    if re.fullmatch(r"\[TABLE\s+\d+\]", first):
        first = next(rows, "")
    header = [_plain(cell).strip() for cell in first.strip("|").split("|")]
    if len(header) < 3 or header[0] != "题号":
        return False
    counts = []
    for cell in header[1:]:
        match = re.fullmatch(r"(\d+)\s*(?:[~～\-–—至]\s*(\d+))?", cell)
        if not match:
            return False
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end < start:
            return False
        counts.append(end - start + 1)
    answer = next(rows, "")
    # Markdown table exports may insert a separator between header and answers.
    if re.fullmatch(r"[\s|:\-]+", answer):
        answer = next(rows, "")
    cells = [_plain(cell).strip() for cell in answer.strip("|").split("|")]
    return (len(cells) == len(header) and cells[0] == "答案"
            and all(re.fullmatch(r"[A-H]+", cell) and len(cell) == count
                    for cell, count in zip(cells[1:], counts)))


def infer_structure(text: str) -> tuple[dict, str, str]:
    """只生成证据明确的题号/分区候选；推断题号、材料边界列入 unresolved。"""
    lines = text.splitlines()
    sections, unresolved, groups, image_questions = [], [], [], []
    active = None
    seen_headings = set()
    appendix_start = len(lines)
    from workflow.denoise import extract_meta
    paper_title = _title_key(extract_meta(lines).get("paper_title", ""))
    for index, raw in enumerate(lines):
        if (active is not None and paper_title and _title_key(raw) == paper_title
                and _answer_summary_follows(lines[index + 1:])):
            appendix_start = index
            unresolved.append({"code": "ANSWER_APPENDIX_DETECTED", "line": index + 1,
                               "message": "重复卷名后紧随题号与答案汇总表，已从卷名处分离为答案附录候选"})
            break
        plain_line = _plain(raw)
        line = plain_line.strip()
        heading = HEADING.match(line)
        kind = next((name for hint, name in TYPE_HINTS if hint in heading.group(2)), None) if heading else None
        if heading and kind:
            key = (heading.group(1), kind)
            if key in seen_headings:
                # 重复大题标题可能是答案附录，也可能是另一份试卷，不能静默并入。
                appendix_start = index
                if looks_like_answers(lines[index:]):
                    # 明确是答案册（题号+答案字母/解析），保留为可核对的答案附录，不当作第二份卷子。
                    unresolved.append({"code": "ANSWER_APPENDIX_DETECTED", "line": index + 1,
                                       "message": "文末重复标题之后是答案册（题号+答案字母/解析），已分离为答案附录候选"})
                else:
                    unresolved.append({"code": "REPEATED_SECTION_APPENDIX", "line": index + 1,
                                       "message": "大题标题重新出现；其后内容单独保留，需核对是答案附录还是另一份试卷"})
                break
            seen_headings.add(key)
            count = re.search(r"(?:共\s*)?(\d+)\s*小题", line)
            active = {"type": kind, "title": line, "source_line": index + 1,
                      "questions": [], "question_locations": [],
                      "declared_count": int(count.group(1)) if count else None,
                      "material_boundaries": []}
            sections.append(active)
            continue
        if active is None:
            continue
        references = guide_numbers(line)
        if material_label(line) or references:
            boundary_index = material_start(lines, index) if material_label(line) else index
            active["material_boundaries"].append({"label": line, "line": boundary_index + 1,
                                                   "references": references})
        match = QUESTION.match(line)
        if active["type"] == "书面表达":
            # 题号只可能在任务开头；正文里的数字列表不能仅凭连续性视为题号。
            prior = [n for section in sections[:-1] for n in section["questions"]]
            preceding = lines[active["source_line"]:index]
            if (not prior or not match or int(match.group(1)) != max(prior) + 1
                    or any(_plain(item).strip() for item in preceding)
                    or not re.search(r"(?:\bwrite\s+\S.+|请[^\n]*写[^\n]*\S|写一[篇封].+)",
                                     line[match.end():], re.I)):
                match = None
        numbers = [int(match.group(1))] if match else []
        # 表格/文章里的空位题号：只认明确下划线或全角空格边界，不扫普通数字。
        if not numbers:
            numbers = blank_numbers(plain_line)
        for number in numbers:
            active["questions"].append(number)
            active["question_locations"].append({"number": number, "line": index + 1,
                                                 "kind": "question" if match else "blank"})
        if "[IMG " in raw and active["questions"]:
            image_questions.append(active["questions"][-1])
    previous = 0
    for section_index, section in enumerate(sections):
        end_line = sections[section_index + 1]["source_line"] - 1 if section_index + 1 < len(sections) else appendix_start
        section["source_end_line"] = end_line
        nums = section["questions"]
        # 完形同一题号在文章空位和选项行各出现一次是正常结构。
        question_lines = [x["number"] for x in section["question_locations"] if x["kind"] == "question"]
        duplicates = [n for n, count in Counter(question_lines).items() if count > 1]
        if duplicates:
            unresolved.append({"code": "DUPLICATE_QUESTION_NUMBER", "line": section["source_line"],
                               "message": f"{section['title']} 内题号重复：{duplicates}"})
        section["questions"] = nums = sorted(set(nums))
        declared = section["declared_count"]
        if not nums and declared and previous:
            nums = list(range(previous + 1, previous + declared + 1))
            section["questions"] = nums
            section["numbers_inferred"] = True
            unresolved.append({"code": "QUESTION_NUMBER_INFERRED", "line": section["source_line"],
                               "message": f"{section['title']} 未读到明确题号，候选编号 {nums} 须核对原卷"})
        if declared and declared != len(nums):
            unresolved.append({"code": "QUESTION_COUNT_MISMATCH", "line": section["source_line"],
                               "message": f"{section['title']} 声明 {declared} 小题，实际定位 {len(nums)} 个"})
        if not nums:
            unresolved.append({"code": "QUESTION_BOUNDARY_MISSING", "line": section["source_line"],
                               "message": f"{section['title']} 未能定位题号"})
            continue
        if previous and nums[0] <= previous:
            unresolved.append({"code": "NUMBERING_RESTART", "line": section["source_line"],
                               "message": "题号跨大题重置或重复；需要按原卷分区定位，不能自动按全卷题号合并"})
        previous = max(previous, max(nums))
        if section["type"] in GROUP_TYPES:
            boundaries = section["material_boundaries"]
            partitions = []
            if boundaries:
                for offset, boundary in enumerate(boundaries):
                    end = boundaries[offset + 1]["line"] if offset + 1 < len(boundaries) else end_line + 1
                    members = sorted({x["number"] for x in section["question_locations"] if boundary["line"] < x["line"] < end})
                    if boundary.get("references") and members != sorted(set(boundary["references"])):
                        unresolved.append({"code": "GUIDE_QUESTION_MISMATCH", "line": boundary["line"],
                                           "message": f"分段导语题号 {boundary['references']} 与实际题号 {members} 不一致"})
                    if members:
                        partitions.append({"questions": members, "type": section["type"],
                                           "boundary_method": "group_leadin" if boundary.get("references") else "material_heading",
                                           "source_start_line": boundary["line"], "source_end_line": end - 1})
            if partitions and set(n for p in partitions for n in p["questions"]) == set(nums):
                groups.extend(partitions)
            else:
                groups.append({"questions": nums, "type": section["type"], "boundary_method": "section_candidate"})
                if section["type"] in {"完形填空", "阅读理解", "听力长对话理解"}:
                    unresolved.append({"code": "GROUP_BOUNDARY_REVIEW", "line": section["source_line"],
                                       "message": f"{section['title']} 的材料/对话分组需要核验，未将分区等同于最终题组"})
    if not sections:
        unresolved.append({"code": "NO_RELIABLE_SECTIONS", "message": "未定位可靠的大题标题，需要按原页整理结构"})
    structure = {"version": VERSION, "status": "candidate", "sections": sections, "groups": groups,
                 "image_questions": sorted(set(image_questions)), "unresolved": unresolved,
                 "requires_visual_review": "[IMG " in text or "[TABLE" in text,
                 "coverage_verified": False}
    body_lines = lines[:appendix_start]
    # 文末重复卷名属于答案册页眉，不能留在最后一题的正文/题干里。
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    if body_lines and paper_title and _title_key(body_lines[-1]) == paper_title:
        body_lines.pop()
    return structure, "\n".join(body_lines).rstrip() + "\n", "\n".join(lines[appendix_start:]).strip()


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    data = json.dumps(value, ensure_ascii=False, indent=2) + "\n" if not isinstance(value, str) else value
    path.write_text(data, encoding="utf-8")


def _pdf(source, out, config):
    from workflow import ocr_backend
    result = ocr_backend.read_pdf(source, out / "pdf", config=config)
    pages = result["pages"]
    warnings = list(result.get("warnings", []))
    for page in pages:
        if not page.get("needs_ocr"):
            continue
        detected = ocr_backend.recognize(source, out / "ocr", force_ocr=True,
                                         pages=str(page["page"]), config=config)
        recognized = next((p for p in detected["pages"] if p["page"] == page["page"]), None)
        if not recognized or not str(recognized.get("text", "")).strip():
            raise ValueError(f"第 {page['page']} 页 OCR 没有文字结果")
        page["native_text"] = page.get("text", "")
        page["native_blocks"] = page.get("blocks", [])
        page["blocks"] = []  # 文字层坐标不能冒充 OCR 文字的坐标。
        page["text"] = recognized["text"]
        page["method"] = "ocr"
        page["ocr_artifacts"] = detected.get("artifacts")
        warnings.extend(detected.get("warnings", []))
    result["warnings"] = warnings
    result["text"] = "\n\n".join(f"[PAGE {p['page']}]\n{p.get('text', '')}" for p in pages)
    return result


def extract_source(source: Path, destination: Path, config=None) -> dict:
    """原件只读，原生/OCR 结果留存；不伪造答案、题号覆盖或审核通过。"""
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    original_hash = mf.sha256_file(source)
    # 每次执行独立证据目录；不覆盖上一轮图片/OCR 文件。
    evidence = Path(tempfile.mkdtemp(prefix="extract-", dir=str(destination)))
    if source.suffix.lower() == ".docx":
        from workflow.docx_extract import extract_docx
        result = extract_docx(source, evidence)
        result.setdefault("pages", [])
        # 正常 Word 保留原生文字/格式。文字极少的图片型 Word 才补充视觉识别。
        if len(re.sub(r"\[IMG[^\]]*\]|\s", "", result["text"])) < 80 and result.get("media"):
            from workflow import ocr_backend
            supplements = []
            for item in result["media"]:
                image = evidence / "media" / item["name"]
                if image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
                    result.setdefault("warnings", []).append(f"无法 OCR 的嵌入图片：{image.name}")
                    continue
                ocr = ocr_backend.recognize(image, evidence / "ocr", config=config)
                supplements.append({"image": item["name"], "pages": ocr["pages"], "artifacts": ocr.get("artifacts")})
                result["text"] = result["text"].replace(f"[IMG {item['name']}]", f"[IMG {item['name']}]\n" + "\n".join(p.get("text", "") for p in ocr["pages"]))
                result.setdefault("warnings", []).extend(ocr.get("warnings", []))
            result["image_ocr"] = supplements
        # 给既有预览器兼容的稳定媒体位置，旧同名图片先备份。
        for item in result.get("media", []):
            name = item["name"]
            if Path(name).name != name:
                raise ValueError("不合法媒体文件名")
            target = destination / "media" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.copy2(target, target.with_name(target.name + ".bak"))
            shutil.copy2(evidence / "media" / name, target)
    elif source.suffix.lower() == ".pdf":
        result = _pdf(source, evidence, config)
        # 文字型 PDF 的题图保留为可预览引用；坐标在证据中，图文归属不凭猜测放行。
        for page in result.get("pages", []):
            if page.get("method") == "ocr":
                continue  # 扫描整页图保留在证据中，不再作为题图重复插入。
            refs = []
            for index, image in enumerate(page.get("images", []), 1):
                original = Path(image["path"])
                name = f"pdf-page-{page['page']}-image-{index}{original.suffix.lower()}"
                target = destination / "media" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.copy2(target, target.with_name(target.name + ".bak"))
                shutil.copy2(original, target)
                image["preview_name"] = name
                refs.append(f"[IMG {name}]")
            if refs:
                page["text"] += "\n" + "\n".join(refs)
                result.setdefault("warnings", []).append(f"PDF 第 {page['page']} 页图片已保留；正文中的引用为页级候选，题目归属和图文位置须按 bbox 核对")
        result["text"] = "\n\n".join(f"[PAGE {p['page']}]\n{p.get('text', '')}" for p in result["pages"])
    elif source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        from workflow import ocr_backend
        result = ocr_backend.recognize(source, evidence / "ocr", config=config)
        result["text"] = "\n\n".join(p.get("text", "") for p in result["pages"])
    else:
        raise ValueError("原卷只支持 docx、pdf 或图片；旧 doc 请先转换为 docx")
    text = result.get("text", "")
    if not text.strip():
        raise ValueError("提取正文为空，不能完成解析阶段")
    if mf.sha256_file(source) != original_hash:
        raise ValueError("提取期间原文件发生变化，请重试")
    from workflow import denoise
    metadata = denoise.extract_meta(text.splitlines())
    structure, body, appendix = infer_structure(text)
    for conflict in metadata.get("conflicts") or []:
        structure["unresolved"].append({"code": "SOURCE_METADATA_CONFLICT", "message": str(conflict)})
    structure["source_sha256"] = original_hash
    structure["layout_required"] = bool(structure.get("requires_visual_review") or
                                         any(issue.get("code") in {"GROUP_BOUNDARY_REVIEW", "QUESTION_BOUNDARY_MISSING"}
                                             for issue in structure["unresolved"]))
    structure["layout_review_command"] = "python -m workflow.layout_review --help" if structure["layout_required"] else None
    warnings = result.get("warnings", [])
    if any(p.get("method") == "ocr" for p in result.get("pages", [])) or result.get("image_ocr"):
        structure["unresolved"].append({"code": "OCR_VISUAL_REVIEW", "message": "OCR 结果需核对题号、音标、画线、表格和图文顺序，未提供逐字可靠性证明"})
    if any(issue.get("code") == "OCR_VISUAL_REVIEW" for issue in structure["unresolved"]) or warnings:
        structure["layout_required"] = True
        structure["layout_review_command"] = "python -m workflow.layout_review --help"
    if warnings:
        structure["extraction_warnings"] = warnings
        structure["unresolved"].append({"code": "EXTRACTION_WARNING", "message": "原生/OCR 提取有警告，详见 extraction.json"})
    result.update({"version": VERSION, "source": str(source), "source_sha256": original_hash,
                   "evidence_dir": str(evidence.relative_to(destination)), "structure_status": "candidate"})
    _write(destination / "原始正文.txt", text)
    _write(destination / "正文.txt", body)
    _write(destination / "附录.txt", appendix)
    _write(destination / "extraction.json", result)
    _write(destination / "structure.json", structure)
    _write(destination / "metadata.json", dict(metadata, original_sha256=original_hash,
                                               source_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest()))
    return {"passed": True, "version": VERSION, "source_sha256": original_hash,
            "characters": len(body), "sections": len(structure["sections"]),
            "questions": sum(len(s["questions"]) for s in structure["sections"]),
            "unresolved": structure["unresolved"], "evidence_dir": result["evidence_dir"]}


def run(batch_dir: Path) -> dict:
    batch_dir = Path(batch_dir)
    manifest = mf.load(batch_dir)
    source = manifest.get("inputs", {}).get("source", {}).get("path")
    if not source:
        raise ValueError("批次缺少 source 输入")
    result = extract_source(Path(source), batch_dir / "01_source")
    _write(batch_dir / "07_review" / "extraction-report.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args.batch_dir)
    except Exception as exc:
        _write(args.batch_dir / "07_review" / "extraction-report.json", {"passed": False, "error": str(exc)})
        print(f"原卷提取失败：{exc}", file=sys.stderr)
        return 1
    print(f"复合提取完成：{report['characters']} 字符，{report['sections']} 个分区，{report['questions']} 个候选题号；{len(report['unresolved'])} 项待核验")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
