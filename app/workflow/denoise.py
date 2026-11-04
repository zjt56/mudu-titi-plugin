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

# 命中即视为整卷说明 / 卷头信息，不进题目正文
META_PATTERNS = (
    ("paper_title", re.compile(r"(?:初中学业水平考试|学业水平考试|中考|模拟|模拟考试|统一考试|质量检测|单元测验|月考试卷)[试卷]?")),
    ("exam_duration", re.compile(r"考试时间\s*[:：]?\s*\d{1,3}\s*分钟")),
    ("total_score", re.compile(r"(?:满分|总分)\s*[:：]?\s*\d{1,3}\s*分")),
    ("page_note", re.compile(r"本试卷共\s*\d+\s*页")),
    ("instruction", re.compile(r"(?:答题前|请在答题卡|请将答案|考生须知|注意事项)")),
)

# 命中即整行丢弃的强噪声
NOISE_PATTERNS = (
    re.compile(r"扫码(?:练|听|查看|观看|获取)"),
    # 只丢弃完整的答案册标签或明确链接；自由文本可能是题干或选项。
    re.compile(r"^(?:答案册|答案见答案册|答案(?:册)?链接\s*[:：]\s*https?://[^\s]+)\s*$", re.I),
    re.compile(r"^[\s\-_=*·•—]+$"),                       # 纯分隔线
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
    r"(?:初中学业水平考试|学业水平考试|中考|模拟考试|统一考试|质量检测|单元测验|月考试卷)"
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


def extract_meta(lines: list[str]) -> dict:
    """从卷头若干行里抽取元信息，不假设一定存在，抽不到就不写该键。"""
    # 只看卷头；题目中的年份、地名不能覆盖试卷来源。移除排版标记后再识别。
    header = []
    for index, raw in enumerate(lines[:40], 1):
        text = re.sub(r"<[^>]+>", "", raw).replace("**", "").strip()
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
                   (r"中考|初中学业水平考试", "中考真题"))
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
    return any(pattern.search(stripped) for pattern in NOISE_PATTERNS)


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
            normalized = re.sub(r"<[^>]+>", "", stripped).replace("**", "").strip()
            numbered_header = (bool(_numbered_title(normalized)) and
                               any(e.startswith(f"正文第{index}行：") for e in meta.get("evidence", [])))
            if _looks_like_question_line(stripped) and not numbered_header:
                kept.append(line)
                continue
            # 只剔除一次卷名，避免把重复出现相同文字的正常题干误删
            if "paper_title" in meta_hits and seen_title and not _strong_meta(stripped):
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
    """带明确题号（`12.` 或 `( )12.`）的行属于题目，不当作元信息剔除。"""
    return bool(re.search(r"(?:[(（]\s*[)）]\s*)?\b\d{1,3}\s*[.．、]\s*\S", stripped))
