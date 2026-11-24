# -*- coding: utf-8 -*-
"""正文去噪与元信息抽取。

把「属于整卷、但不属于任何一道题」的信息从题目正文里剥离出来，并放进专门的
元信息字段；题目（题干/材料/选项）里一律不再保留。

- 元信息：卷名、年份、地区、考试时长、总分、整卷作答说明等，抽取为结构化键值；
- 噪声：扫码提示、答案册链接、页眉 logo/装饰条、分隔线、来源水印等，直接丢弃；
- 排除：页眉 logo、广告、二维码等媒体文件在结构层标记为 excluded，不挂到题目上。

本模块只做确定性的文本处理，不改动词语与标点，只做整行/整段级剔除。凡是判不准
的行一律保留并记入 `unresolved`，交由机器自检报告出来，不擅自删除可能有用的内容。
"""
from __future__ import annotations

import re
from bisect import bisect_right
from copy import deepcopy
from hashlib import sha256

# 新旧卷头名称均属于中考：例如 2014 年的「初中毕业学业考试」。
JUNIOR_EXAM = r"初中(?:毕业(?:生)?)?学业(?:水平)?考试"

# 命中即视为整卷说明 / 卷头信息，不进题目正文
META_PATTERNS = (
    ("paper_title", re.compile(rf"(?:{JUNIOR_EXAM}|学业水平考试|中考|模拟|模拟考试|统一考试|质量检测|单元测验|月考试卷)[试卷]?")),
    ("exam_duration", re.compile(r"考试时间\s*[:：]?\s*\d{1,3}\s*分钟")),
    ("total_score", re.compile(r"(?:满分|总分)\s*[:：]?\s*\d{1,3}\s*分")),
    ("page_note", re.compile(r"本试卷共\s*\d+\s*页")),
    ("instruction", re.compile(r"(?:答题前|请在答题卡|请将答案|考生须知|注意事项)")),
)

# 命中即整行丢弃的强噪声
NOISE_PATTERNS = (
    re.compile(r"扫码(?:练|听|查看|观看|获取)"),
    # 只丢弃完整的答案册标签或明确链接；自由文本可能是题干或选项。
    re.compile(r"^(?:答案册|答案见答案册|答案(?:册)?链接\s*(?:☞\s*答案册\S*|[:：]\s*(?:https?://[^\s]+|答案册\S*)))\s*$", re.I),
    # 纯分隔线只认装饰字符。下划线是本流水线的作答空位标记（`_{2,}`）：
    # 整行等宽下划线是无号题（如古诗画线题）的作答区，属于题目内容；
    # 当分隔线丢掉会让该大题窗口失去作答证据，进而整道题不产出来源单元。
    re.compile(r"^[\s\-=*·•—]+$"),                        # 纯分隔线（不含作答下划线）
    # Only an explicit publisher watermark is disposable; URLs can be question content.
    re.compile(r"^\s*(?:来源\s*[:：]?\s*)?学科网(?:\s*(?:https?://)?www\.zxxk\.com/?)?\s*$", re.I),
    re.compile(r"^\s*第\s*\d+\s*页\s*(?:共\s*\d+\s*页)?"),  # 页脚
    re.compile(r"^\s*[-—]\s*\d{1,4}\s*[-—]\s*$"),           # -101- 式页码
)

YEAR = re.compile(r"(19|20)\d{2}\s*年")
REGION = re.compile(r"([\u4e00-\u9fa5]{2,8}?(?:省|自治区|市|县|区))")
NUMBERED_TITLE = re.compile(
    r"^\d{1,3}\s*[.．、]\s*"
    r"(?P<title>(?:(?:19|20)\d{2}\s*年\s*)?"
    r"(?:(?:安徽|北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾)(?:省|市)?|"
    r"(?:广西|内蒙古|西藏|宁夏|新疆)(?:壮族自治区|回族自治区|维吾尔自治区|自治区)?|全国)?"
    rf"(?:{JUNIOR_EXAM}|学业水平考试|中考|模拟考试|统一考试|质量检测|单元测验|月考试卷)"
    r"(?:试卷|试题|英语)?(?:\s*[（(][^（）()]{1,20}[）)])?)$"
)


def _numbered_title(text: str) -> str | None:
    match = NUMBERED_TITLE.fullmatch(text)
    return match.group("title").strip() if match else None


def _region_of(head: str) -> str | None:
    """取地区，去掉被包含的年份字（安徽省，而非 年安徽省）。"""
    best = None
    for match in REGION.finditer(head):
        name = match.group(1)
        # 去掉误吞的前导年份/日期字
        name = re.sub(r"^[年月日\d]+", "", name)
        if len(name) >= 2:
            best = name
    return best


def _clean_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _plain_line(raw: str) -> str:
    """剥离行内 [IMG]、HTML 标签与加粗星号，用于识别元信息或题号结构（不改原文本）。"""
    no_img = re.sub(r"\[IMG\s+[^\]]+\]", "", raw)
    return re.sub(r"<[^>]+>", "", no_img).replace("**", "").strip()


def extract_meta(lines: list[str]) -> dict:
    """从卷头若干行里抽取元信息，不假设一定存在，抽不到就不写该键。"""
    # 只看卷头；题目中的年份、地名不能覆盖试卷来源。移除排版标记后再识别。
    from . import sync_practice
    sync = sync_practice.looks_like_sync_practice("\n".join(lines[:40]))
    header = []
    for index, raw in enumerate(lines[:40], 1):
        if sync and sync_practice.is_section_title(raw):
            # Workbook topic containers introduce content. Their introductory
            # prose can mention arbitrary cities without setting paper region.
            break
        # 卷头行首偶尔带装饰性图标占位（如 `[IMG image3.jpeg]1.2025年安徽省初中学业水平考试`），
        # 提取纯文本元信息前先剥离 [IMG ...]，避免把页眉图标带进 source/paper_title 并污染下游附件。
        text = _plain_line(raw)
        title = _numbered_title(text)
        if re.match(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+[.．、]|[（(]\s*[）)]\s*\d+[.．、]|\d+[.．、]\s*\S)", text) and not title:
            break
        header.append((index, title or text, raw.strip()))
    head = "\n".join(text for _, text, _ in header)
    meta: dict = {}
    titles = [(index, text, raw) for index, text, raw in header if META_PATTERNS[0][1].search(text)]
    if titles:
        meta["source"] = titles[0][1]
        meta["paper_title"] = titles[0][1]
        meta["evidence"] = [f"正文第{index}行：{raw}" for index, _, raw in titles]
        classes = ((r"模拟", "中考模拟"), (r"复习", "中考复习"),
                   (r"期末", "期末试题"), (r"期中", "期中试题"),
                   (r"月考", "月考试题"), (r"开学", "开学试题"),
                   (r"单元测验", "单元测验"), (r"同步练习", "同步练习"),
                   (r"中考|" + JUNIOR_EXAM, "中考真题"))
        for pattern, category in classes:
            if re.search(pattern, head):
                meta["source_type"] = category
                break
        years = sorted({int(y) for y in re.findall(r"((?:19|20)\d{2})\s*年", "\n".join(t for _, t, _ in titles))})
        if len(years) > 1:
            meta["conflicts"] = [f"卷头来源年份冲突：{years}"]
    year_match = YEAR.search("\n".join(t for _, t, _ in titles)) or YEAR.search(head)
    if year_match:
        y = year_match.group(0)
        digits = re.search(r"((?:19|20)\d{2})", y)
        if digits:
            meta["year"] = int(digits.group(1))
    region = _region_of("\n".join(t for _, t, _ in titles)) or _region_of(head)
    if region:
        meta["region"] = region
    if sync:
        meta.setdefault("source_type", "同步练习")
    for key, pattern in META_PATTERNS:
        match = pattern.search(head)
        if match:
            if key == "exam_duration":
                num = re.search(r"\d{1,3}", match.group(0))
                if num:
                    meta["duration_minutes"] = int(num.group(0))
            elif key == "total_score":
                num = re.search(r"\d{1,3}", match.group(0))
                if num:
                    meta["total_score"] = int(num.group(0))
            elif key == "paper_title" and "paper_title" not in meta:
                meta["paper_title"] = match.group(0).strip()
    return meta


def is_meta_line(line: str) -> list[str]:
    """返回该行命中的元信息类别名列表（空列表表示不是元信息行）。"""
    hits = []
    for key, pattern in META_PATTERNS:
        if pattern.search(line):
            hits.append(key)
    return hits


def is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    plain = _plain_line(stripped)
    return any(pattern.search(stripped) or (plain and pattern.search(plain)) for pattern in NOISE_PATTERNS)


def clean_body(text: str) -> tuple[str, list[dict], list[dict]]:
    """把正文去噪并剥离元信息。

    返回 (cleaned_text, meta, removed)：
    - cleaned_text：去掉噪声行与元信息行后的正文；
    - meta：抽取到的元信息字典；
    - removed：被剔除的行记录 [{"line_no", "text", "kind", "reason"}]，
      便于审计与自检，不静默删除。
    """
    lines = _clean_lines(text)
    meta = extract_meta(lines)
    kept: list[str] = []
    removed: list[dict] = []
    seen_title = False
    for index, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if is_noise_line(line):
            removed.append({"line_no": index, "text": stripped, "kind": "noise",
                            "reason": "噪声行（广告/扫码/水印/页码/分隔线）"})
            continue
        # 大题标题包含局部满分/作答说明，但它是结构边界，不能按整卷总分剔除。
        if re.match(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+|[一二三四五六七八九十]+)[.．、]", stripped):
            kept.append(line)
            continue
        meta_hits = is_meta_line(line)
        if meta_hits:
            # 卷名/年份/地区页眉可能同时含题号，保守起见只在明确是整卷说明时剔除
            normalized = _plain_line(stripped)
            in_header_evidence = any(e.startswith(f"正文第{index}行：") for e in meta.get("evidence", []))
            numbered_header = bool(_numbered_title(normalized)) and in_header_evidence
            if _looks_like_question_line(stripped) and not numbered_header:
                kept.append(line)
                continue
            # paper_title 仅允许在卷头证据行内剔除一次；进入大题/题目正文后出现“模拟/中考”字样属于正文或真题来源标签，绝不误删。
            if "paper_title" in meta_hits and not _strong_meta(stripped) and (seen_title or not in_header_evidence):
                kept.append(line)
                continue
            if "paper_title" in meta_hits:
                seen_title = True
            removed.append({"line_no": index, "text": stripped, "kind": "meta",
                            "reason": "元信息：" + "/".join(meta_hits)})
            continue
        kept.append(line)
    return "\n".join(kept), meta, removed


def _strong_meta(stripped: str) -> bool:
    """含考试时长/总分/页数/注意事项的整卷说明行，一定是元信息。"""
    return any(name in ("exam_duration", "total_score", "page_note", "instruction")
               for name in is_meta_line(stripped))


def _looks_like_question_line(stripped: str) -> bool:
    """带明确题号（`12.`、`**2****.**` 或 `( )12.`）的行属于题目，不当作元信息剔除。"""
    normalized = _plain_line(stripped)
    return bool(re.search(r"(?:[(（]\s*[)）]\s*)?\b\d{1,3}\s*[.．、]\s*\S", normalized))


def deletion_map(lines: list[str]) -> tuple[dict[int, int], dict[int, int]]:
    """正文去噪的 1 起行号映射 (raw→clean, clean→raw)。

    结构单元与确认结构由 runs 在清洗后的正文上确认，而答案/证据按原文件行号
    引用；两边必须用同一张删除表换算，不能假设序号一致。无删除时返回空表，
    调用方按恒等处理。
    """
    text = "\n".join(lines)
    cleaned, _, removed = clean_body(text)
    removed_lines = {row["line_no"] for row in removed}
    if not removed_lines:
        return {}, {}
    kept = [n for n in range(1, len(lines) + 1) if n not in removed_lines]
    if len(kept) != len(cleaned.split("\n")):
        return {}, {}
    return ({n: i for i, n in enumerate(kept, 1)},
            {i: n for i, n in enumerate(kept, 1)})


def project_structure(structure: dict, raw_text: str, cleaned_text: str | None = None) -> dict:
    """Project raw extraction coordinates through the exact denoising deletion map.

    Only line coordinates change; printed numbers, evidence strings and source
    content remain untouched. This is for structure.json, not AI/confirmed units
    whose coordinates already refer to the cleaned body.
    """
    cleaned, _, removed = clean_body(raw_text)
    if cleaned_text is not None and cleaned != cleaned_text:
        raise ValueError("SOURCE_COORDINATE_MISMATCH: current cleaned body differs from denoising projection")
    result = deepcopy(structure)
    if not removed:
        return result
    proof = {"raw_sha256": sha256(raw_text.encode("utf-8")).hexdigest(),
             "cleaned_sha256": sha256(cleaned.encode("utf-8")).hexdigest()}
    if result.get("coordinate_projection") == proof:
        return result
    if result.get("coordinate_projection"):
        raise ValueError("SOURCE_COORDINATE_MISMATCH: structure projection belongs to another body")
    raw_lines = raw_text.splitlines()
    # `unresolved` 是提取/OCR 诊断，不是题目锚点；它的 line 可能来自原始全文
    # （含答案册/附录，超出已清理正文）。诊断行不参与坐标投影，否则一个
    # QUESTION_NUMBER_INFERRED 的越界行就会把整个结构闸门卡死。
    unresolved = result.pop("unresolved", None)
    # 书面表达常被提取器把文末答案/听力材料标题并入 section end，导致 end 超出
    # 实际正文（例如 19 年安徽卷 319 vs 315）。作文正文以当前 body 为准，超出
    # body 的尾部只能是附录/页脚，夹到最后一行；普通大题不享受此夹取。
    for section in result.get("sections") or []:
        if "书面表达" not in str(section.get("type") or ""):
            continue
        end = section.get("source_end_line")
        if type(end) is int and end > len(raw_lines):
            section["source_end_line"] = len(raw_lines)
    removed_lines = {row["line_no"] for row in removed}
    # splitlines() drops one trailing empty line while clean_body uses split("\n").
    # Keep the mapping in the same coordinate system as all source consumers.
    retained = [n for n in range(1, len(raw_lines) + 1) if n not in removed_lines][:len(cleaned.splitlines())]
    # Some extractors record an inclusive section end on the terminal empty line
    # represented by a final newline. It is a boundary, not a source anchor;
    # accept only that one end-coordinate and project it to the last retained line.
    terminal_boundary = bool(raw_text) and raw_text.endswith(("\n", "\r"))
    direct = {n: i for i, n in enumerate(retained, 1)}
    normalize = lambda text: re.sub(r"</?(?:u|sup|sub|em)>", "", str(text)).replace("**", "").strip()
    for section in result.get("sections") or []:
        at, title = section.get("source_line"), section.get("title")
        if type(at) is int and title and (not 1 <= at <= len(raw_lines)
                                          or normalize(raw_lines[at - 1]) != normalize(title)):
            raise ValueError(f"SOURCE_COORDINATE_MISMATCH: section heading at raw line {at} does not match current body")
    starts = {"source_line", "source_start_line", "start_line", "heading_line"}
    ends = {"source_end_line", "end_line"}
    points = {"line", "line_no"}

    def project(value, key=""):
        if isinstance(value, dict):
            # Appendix detection is a diagnostic boundary after the source body,
            # not a question/source anchor. Preserve its original marker line even
            # when the detector reports the first appendix line beyond raw text.
            if value.get("code") == "ANSWER_APPENDIX_DETECTED":
                return deepcopy(value)
            return {k: project(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [project(v) for v in value]
        if type(value) is not int or key not in starts | ends | points:
            return value
        is_terminal_end = key in ends and terminal_boundary and value == len(raw_lines) + 1
        if is_terminal_end:
            if not retained:
                raise ValueError(f"SOURCE_COORDINATE_MISMATCH: {key}={value} has no retained body boundary")
            return len(retained)
        if not 1 <= value <= len(raw_lines):
            raise ValueError(f"SOURCE_COORDINATE_MISMATCH: {key}={value} outside raw body 1..{len(raw_lines)}")
        if value in direct:
            return direct[value]
        if key in ends:
            mapped = bisect_right(retained, value)
        else:
            raise ValueError(f"SOURCE_COORDINATE_MISMATCH: {key}={value} points to a removed line; cannot move an anchor to another question")
        if not 1 <= mapped <= len(retained):
            raise ValueError(f"SOURCE_COORDINATE_MISMATCH: {key}={value} has no retained body boundary")
        return mapped

    result = project(result)
    if unresolved is not None:
        result["unresolved"] = deepcopy(unresolved)
    result["coordinate_projection"] = proof
    return result
