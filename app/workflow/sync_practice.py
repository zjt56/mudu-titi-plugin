# -*- coding: utf-8 -*-
"""同步练习 / 课时同步材料的题型与组题判定口径。

规则数据在 ``references/sync-practice-rules.json``，由 26秋《课时A计划》英语 RJ
七上·八上·九上（学用）正文 854 处大题实测归纳而来：

* 题型、子类、组题/单独题三者一起判定，避免各批次各自解释；
* 只适用于同步练习册/课时同步练习类材料（按课时编排、每课时常分基础段与提升段），
  单元卷、期中期末卷、中高考真题不套用；
* ``unit_kind``（组题/单独题）在结构确认阶段定死，本模块只提供判据，不改写已完成批次。

命令行自检::

    PYTHONPATH=app python3 -m workflow.sync_practice "Ⅰ.根据首字母及汉语提示补全单词"
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "references" / "sync-practice-rules.json"

# 罗马数字题号 / 中文序号前缀
_NUMBER_PREFIX = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|[一二三四五六七八九十]+)\s*[.．、,，)）]\s*")
# 方括号标签：来源（[2025·滁州天长市月考]）、命题特征（[情境题]/[跨学科]/[原创]）
_BRACKET = re.compile(r"[\[【][^\]】]*[\]】]")
# 文末答案区标题：与 extract.ANSWER_HEADING 同一口径，不能各自解释。
_ANSWER_HEADING = re.compile(
    r"^\s*【?\s*(?:参考答案及解析|参考答案与解析|答案与解析|答案及解析|参考答案"
    r"|选择题答案|答案速查表|答案速查|答案)\s*】?\s*[:：]?\s*$")
# 答案行形态：罗马/中文序号接行内答案，或「1~5 ABD」这类区间答案。
_ANSWER_ROW = re.compile(
    r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|[一二三四五六七八九十]+)\s*[.．、,，]\s*(?:答案|解析|\d)"
    r"|^\s*\d{1,4}\s*[~～\-–—至]\s*\d{1,4}\s*[A-Ha-h]"
    r"|^\s*\d{1,4}\s*[.．、]\s*[A-Ha-h]\s*$")


def plain_text(text: str) -> str:
    """只为识别去掉原生提取的字符格式；不改源文件或来源坐标。"""
    value = re.sub(r"\[IMG\s+[^\]]*\]", "", str(text or ""))
    value = re.sub(r"</?(?:u|sup|sub|b|strong|i|em)\b[^>]*>", "", value).replace("**", "")
    # PDF OCR emits ATX headings. Strip formatting only in this recognition
    # copy; original wording and 1-based source coordinates remain untouched.
    return re.sub(r"^\s{0,3}#{1,6}[ \t]+|[ \t]+#+[ \t]*$", "", value)


def normalize(title: str) -> str:
    """去掉题号前缀、方括号标签与空白，得到用于匹配的标题。"""
    text = _BRACKET.sub("", plain_text(title))
    text = _NUMBER_PREFIX.sub("", text)
    text = text.replace("，", ",").replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


@lru_cache(maxsize=4)
def _load_cached(path_text: str) -> dict:
    return json.loads(Path(path_text).read_text(encoding="utf-8-sig"))


def load(path: str | Path | None = None) -> dict:
    return _load_cached(str(path or RULES_PATH))


def rules(path: str | Path | None = None) -> list[dict]:
    return list(load(path).get("rules") or [])


def type_kind(path: str | Path | None = None) -> dict[str, str]:
    return dict(load(path).get("type_kind") or {})


def kind_for(type_name: str, path: str | Path | None = None) -> str | None:
    """按主类给默认组题/单独题；未知主类返回 None。"""
    return type_kind(path).get(str(type_name or "").strip())


def _matches(rule: dict, text: str) -> bool:
    if any(bad in text for bad in rule.get("none") or []):
        return False
    keywords = rule.get("any") or []
    if not any(word in text for word in keywords):
        return False
    required = rule.get("subtype_any") or []
    if required and not any(word in text for word in required):
        return False
    return True


def classify(title: str, path: str | Path | None = None) -> dict | None:
    """把原卷大题标题映射成 {type, subtype, kind, rule, note}；无匹配返回 None。

    只做“表述 → 题型”的确定性映射；子类为 None 的规则表示需按正文统计
    （如阅读小题数、完形空数），由模型结合正文补齐，不得凭空猜。
    """
    text = normalize(title)
    if not text:
        return None
    for rule in rules(path):
        if _matches(rule, text):
            return {
                "title": text,
                "type": rule.get("type"),
                "subtype": rule.get("subtype"),
                "kind": rule.get("kind") or kind_for(rule.get("type") or "", path),
                "rule": rule.get("id"),
                "note": rule.get("note") or "",
            }
    return None


_SYNC_HINTS = ("课时", "Section A", "Section B", "基础", "提升", "课时A计划", "同步练习")
_OPTION_NUMBER = re.compile(r"[（(]\s*[)）]\s*(\d{1,2})\s*[.．]")
_MIXED = ("完成下列各题", "完成各题", "思维导图", "完成表格", "排序", "判断正误", "信息摘录")


def refine(hit: dict | None, body: str) -> dict | None:
    """用正文把子类补齐：阅读小题数、完形空数、任务型问答/综合、补全对话 7选5/填写单词。

    规则里 ``subtype`` 为 None 表示必须看正文才能定；正文不足以判断时保持 None，
    不猜、不默认。
    """
    if not hit or hit.get("subtype"):
        return hit
    text = plain_text(body)
    # ☆拓展设问 是附在最后一小题后的开放性追问，不是第 N 空/第 N 小题
    text = re.split(r"[☆★]?\s*拓展设问", text)[0]
    hit = dict(hit)
    name = hit.get("type")
    numbers = {match.group(1) for match in _OPTION_NUMBER.finditer(text)}
    if name == "完形填空":
        blank = "\u3000"
        count = len(numbers) or len(re.findall(rf"{blank}\d{{1,2}}{blank}", text))
        if count:
            hit["subtype"] = {5: "5空", 10: "10空", 15: "15空"}.get(count, "其他空数")
    elif name == "阅读理解":
        count = len(numbers)
        if count in (3, 4, 5):
            hit["subtype"] = f"选择{count}题"
    elif name == "任务型阅读":
        if any(word in text for word in _MIXED) or re.search(r"[（(]\s*[)）]\s*\d{1,2}[.．]\s*[A-D][.．]", text):
            hit["subtype"] = "阅读综合"
        elif re.search(r"根据短文内容回答|回答(?:下列|以下)?问题", text):
            hit["subtype"] = "阅读问答"
    elif name == "补全对话":
        letters = set(re.findall(r"(?:^|[\t　])\s*([A-G])[.．]\s*\S", text, re.M))
        if letters == set("ABCDEFG"):
            hit["subtype"] = "7选5"
        elif "特殊疑问词" in hit.get("title", ""):
            hit["subtype"] = "填写单词"
    return hit


def classify_with_body(title: str, body: str, path: str | Path | None = None) -> dict | None:
    return refine(classify(title, path), body)


_UNIT_HINTS = ("单元卷", "单元测试", "期中", "期末", "月考", "模拟", "中考")


# 课时标题：第一课时 / 第1课时 / 第1节课 / 课时1 / Period 1 / Lesson 1。
# 课时标题后常直接跟 Section A（可能没有空格）或全角空格，不能要求必须有空白。
_LESSON = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百零\d]+\s*(?:课时|节课)"
    r"|课时\s*[一二三四五六七八九十百零\d]+"
    r"|(?:Period|Lesson)\s*\d+)"
    r"(?![一二三四五六七八九十百零\d])",
    re.I)
# 与课时平级的版块标题：本身不是大题，也不能被并进上一题组的共享材料。
_SECTION_TITLES = ("综合阅读提升", "单元话题写作", "Pronunciation",
                   "Reading Plus", "Project", "Reflecting")
_UNIT = re.compile(r"^\s*(?:Starter\s+)?Unit\s+\d+\b", re.I)
_ROMAN_HEADING = re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+\s*[.．、]", re.I)
_ITEM_LINE = re.compile(r"^\s*(?:[（(]\s*[）)]\s*)?\d+\s*[.．、)]")


def is_section_title(line: str) -> bool:
    """与课时平级的版块标题（如 Reading Plus / Project / Reflecting）。"""
    plain = plain_text(line).strip().rstrip(":：").strip()
    if not plain:
        return False
    folded = plain.casefold()
    return any(folded == name.casefold() for name in _SECTION_TITLES)


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _section_number(value: str) -> int | None:
    """中文/阿拉伯课时序号：一二三…十、十二、二十一、1、01 都能转成整数。"""
    text = plain_text(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        head, _, tail = text.partition("十")
        if head and head not in _CN_DIGITS:
            return None
        tens = _CN_DIGITS[head] if head else 1
        if tail and tail not in _CN_DIGITS:
            return None
        return tens * 10 + (_CN_DIGITS[tail] if tail else 0)
    return _CN_DIGITS.get(text)


def _lesson_number(line: str) -> int | None:
    """课时标题里的序号；不是课时标题返回 None。"""
    plain = plain_text(line).strip()
    match = re.match(r"^第\s*([一二三四五六七八九十百零〇两\d]+)\s*(?:课时|节课)", plain)
    if not match:
        match = re.match(r"^课时\s*([一二三四五六七八九十百零〇两\d]+)", plain)
    if match:
        return _section_number(match.group(1))
    match = re.match(r"^(?:Period|Lesson)\s*(\d+)", plain, re.I)
    return int(match.group(1)) if match else None


def looks_like_sync_practice(text: str) -> bool:
    """按卷头/课时结构判断，题内来源标签不参与整卷身份投票。"""
    lines = [plain_text(line).strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return False
    # 试卷卷头优先；题内 [2025·期末] 不能否定真实课时标题。
    head = []
    for line in lines[:6]:
        if _ROMAN_HEADING.match(line) or _ITEM_LINE.match(line):
            break
        head.append(_BRACKET.sub("", line))
    if any(re.search(r"(?:单元(?:测试|检测|卷)|期[中末]|月考|中考|模拟).*(?:试题|试卷|测试|检测|卷)$", line)
           for line in head):
        return False
    if any(_LESSON.match(line) for line in lines):
        return True
    heading = "\n".join(head)
    if re.search(r"同步练习|课时\s*A\s*计划|课时同步", heading, re.I):
        return True
    return bool(any(_UNIT.match(line) for line in head)
                and re.search(r"Section\s+[AB]|Grammar\s+Focus|Pronunciation", heading, re.I))


def heading_hit(line: str, path: str | Path | None = None) -> dict | None:
    """只接受标题形态，不把题干/文章里提及的关键词当成大题。"""
    plain = plain_text(line).strip()
    if not plain or _ITEM_LINE.match(plain) or plain.startswith(("[TABLE", "[/TABLE")):
        return None
    text = normalize(plain)
    if text in ("综合阅读提升", "单元话题写作", "☆拓展设问", "拓展设问"):
        return None
    hit = classify(plain, path)
    if not hit:
        return None
    if _ROMAN_HEADING.match(plain):
        return hit
    # 无序号指令标题；不匹配“这篇阅读理解…”等普通叙述。
    if hit["type"] == "书面表达":
        return hit if (text == '书面表达' or re.search(r"(?:请|假如|假设).{0,160}写一[篇封段份]", text)) else None
    if len(text) <= 100 and re.match(
            r"(?:阅读理解|任务型阅读|完形填空|补全对话|单项填空|单项选择|教材语篇回顾|短文填空|选词填空|"
            r"根据|用|填入|按照|将|选出|找出|在下列|写出|按要求|翻译|汉译英|☆?佳句仿写)", text):
        return hit
    return None


def answer_key_start(raw_text: str) -> int | None:
    """文末答案区起始行（1 基）；没有可靠答案区标题时返回 None。

    同步练习册的答案区常紧跟最后一个大题，标题是「参考答案」；若把它算进上一大题
    的来源窗口，答案里的题号会和正文题号撞车（`SOURCE_DUPLICATE_NUMBERS`），
    还会把答案文字当成题干/选项（`QUESTION_NOT_FOUND`、`SOURCE_OPTIONS_INCOMPLETE`）。
    只有标题后面确实成行出现答案（至少 2 行）才认定，避免正文里一句「答案：B」
    被误判成附录。
    """
    lines = [plain_text(line).strip() for line in str(raw_text or "").splitlines()]
    for index, line in enumerate(lines):
        if not _ANSWER_HEADING.match(line):
            continue
        tail = [item for item in lines[index + 1:] if item]
        if sum(1 for item in tail[:40] if _ANSWER_ROW.match(item)) < 2:
            continue
        # 合订练习册可能每课时后都附答案，后面仍有真实课时/大题。
        # 这类中间答案不能作为整份正文的终点；罗马序号行内答案不算新大题。
        if any(_LESSON.match(item) or _UNIT.match(item)
               or (heading_hit(item) and not _ANSWER_ROW.match(item)) for item in tail):
            continue
        return index + 1
    return None


def heading_candidates(raw_text: str, path: str | Path | None = None) -> list[dict]:
    """原文坐标上的大题 occurrence 候选；不生成/改写正式题号或确认单元。"""
    lines = str(raw_text or "").splitlines()
    rows, context, table_depth = [], "", 0
    appendix = answer_key_start(raw_text)
    for index, raw in enumerate(lines, 1):
        if appendix and index >= appendix:
            break
        plain = plain_text(raw).strip()
        if plain.startswith("[TABLE"):
            table_depth += 1
        if table_depth:
            if plain.startswith("[/TABLE"):
                table_depth -= 1
            continue
        if _LESSON.match(plain) or _UNIT.match(plain) or is_section_title(plain):
            context = plain
        hit = heading_hit(raw, path)
        if hit:
            rows.append({"occurrence": len(rows) + 1, "start_line": index,
                         "title": raw, "context": context, **{k: hit[k] for k in ("type", "subtype", "kind", "rule")}})
    for pos, row in enumerate(rows):
        end = rows[pos + 1]["start_line"] - 1 if pos + 1 < len(rows) else len(lines)
        # 课时/容器边界不能附在上一大题的材料里。
        for cursor in range(row["start_line"], end):
            plain = plain_text(lines[cursor]).strip()
            if _LESSON.match(plain) or _UNIT.match(plain) or is_section_title(plain):
                end = cursor
                break
        # 文末答案区不属于任何大题：答案里的题号/选项不得进入来源窗口。
        if appendix and row["start_line"] < appendix <= end:
            end = appendix - 1
        row["end_line"] = max(row["start_line"], end)
    return rows


def lesson_constraints(path: str | Path | None = None) -> list[dict]:
    """课时录入约束（来自 Mudu-English-Upload 的课时口径）；提示词与自检共用一份数据。"""
    return [dict(item) for item in load(path).get("lesson_constraints") or []
            if isinstance(item, dict) and str(item.get("text") or "").strip()]


def audit_lesson_shape(raw_text: str, path: str | Path | None = None) -> list[dict]:
    """课时编排自检：课时缺单元名、空课时、课时跳号。

    只看原卷坐标，不改结构、不改题型，也不阻断闸门；供提取报告与提示词对照。
    """
    if not looks_like_sync_practice(raw_text):
        return []
    lines = str(raw_text or "").splitlines()
    rows = heading_candidates(raw_text, path)
    starts = [row["start_line"] for row in rows]
    appendix = answer_key_start(raw_text) or len(lines) + 1
    lessons: list[dict] = []
    unit, unit_line = "", 0
    for index, raw in enumerate(lines, 1):
        if index >= appendix:
            break
        plain = plain_text(raw).strip()
        if not plain or plain.startswith(("[TABLE", "[/TABLE")):
            continue
        if _UNIT.match(plain):
            unit, unit_line = plain, index
            continue
        number = _lesson_number(plain)
        if number is None:
            continue
        lessons.append({"line": index, "number": number, "title": plain,
                        "unit": unit, "unit_line": unit_line})
    findings: list[dict] = []
    for pos, lesson in enumerate(lessons):
        stop = lessons[pos + 1]["line"] if pos + 1 < len(lessons) else min(appendix, len(lines) + 1)
        if not lesson["unit"]:
            findings.append({"code": "LESSON_UNIT_MISSING", "line": lesson["line"], "title": lesson["title"],
                             "problems": [f"课时「{lesson['title']}」之前没有单元标题，无法确定所属单元"]})
        if not any(lesson["line"] < start < stop for start in starts):
            findings.append({"code": "LESSON_EMPTY", "line": lesson["line"], "title": lesson["title"],
                             "problems": [f"课时「{lesson['title']}」与下一课时之间没有任何大题标题"]})
    grouped: dict[str, list[dict]] = {}
    for lesson in lessons:
        grouped.setdefault(lesson["unit"] or "", []).append(lesson)
    for group in grouped.values():
        for pos, lesson in enumerate(group):
            if lesson["number"] != pos + 1:
                findings.append({"code": "LESSON_NUMBER_GAP", "line": lesson["line"], "title": lesson["title"],
                                 "problems": [f"课时序号不连续：第 {pos + 1} 个课时位置是「{lesson['title']}」"]})
                break
    findings.sort(key=lambda row: row["line"])
    return findings


def policy_fingerprint() -> str:
    """规则或识别实现变化后，不能复用旧结构 AI 缓存。"""
    return hashlib.sha256(RULES_PATH.read_bytes() + Path(__file__).read_bytes()).hexdigest()


def prompt_block(path: str | Path | None = None, limit: int = 0) -> str:
    """给模型看的紧凑版判定口径（含同步练习册里真实出现过的全部表述）。"""
    data = load(path)
    lines = ["同步练习/课时同步判定口径（仅当本卷是按课时编排的同步练习册时适用）："]
    lines.append("- 题号在每个大写罗马数字大题从 1 重新开始，属正常编排：不得按全卷连续号合并，也不得因此判冲突。")
    lines.append("- 题型必须填目录题型名（语音题/单词拼写/完成句子/单项填空/改错/补全对话/阅读理解…），"
                 "不能把大题标题、题干或答题指令抄进题型字段；子类填不出就留空，不得自造。")
    lines.append("- 卷末「参考答案/答案与解析」不属于最后一个大题：其窗口必须在答案标题前结束，答案行不得当题干/选项。")
    lines.append("- 一行内并排的两个小题（`1.U　v (　　　)　　　　2.W　x (　　　)`）都要认出题号；"
                 "`B:1.<u>　　</u>` 这类说话人前缀后的题号也是题号。")
    lines.append("- 完形/短文填空的材料行里 `<u>　n　</u>`、`2.　　`、`Tony.1.____` 这类是空位号（子题号本体），"
                 "不是印刷题号，也不与下方 `( )n.` 选项行重复；只有行首/选项行的 `( )n.`/`n.` 才是独立印刷题号。"
                 "不得把材料内编号空格当重复题号或漏题，也不得因 `</u>.` 后紧跟句点而误识别。")
    lines.append("- 小题只存在于图片中（窗口正文只有 `[IMG …]`）按图片能力限制记录，不阻断切题、不进人工队列。")
    lines.append("- 原卷方括号提示标记（`[2025·六安月考]`、`[新考法·跨学科]`、`[情境题]`、`[改编]` 等）"
                 "及无年份的[传统文化]/[安徽人文]/[榜样人物]/[经典谚语]/[熟词生义]是原文的一部分。"
                 "逐题标记留在所属题/子题题干，大题标记留在所属题组作答指令；完形/填空句内标记可留原材料。"
                 "必须完整原样保留，不得只留年份、移到别题、只存source_section元数据或解析，不得删除或改写。")
    lines.append("- 共用一段语篇/对话/表格/方框/图片的整题记 group；每小句独立成题的记 standalone。")
    # 版面/呈现口径（来自课时作业逐题型实测，避免“录对了内容但页面变形”）
    lines.append("- 版面规约（板块决定页面长什么样，必守）：①题干只写作答指令、渲染在材料上方，"
                 "题目内容与空档不属于指令；②空档统一 `______`（6 个下划线），首字母题首字母紧贴空档（`c______`）、"
                 "非首字母空档两侧留空格；③组题里材料已带齐题号（`__51__`/`51.`/`　51　`）时，子题 stem 只写空壳"
                 "`______`，不要重复题号或整句，否则底部整串重复；④完形子题 stem 写题号 `1.`、恰好 4 个选项"
                 "（A–D 连续），☆拓展设问的设问留在 stem 里；⑤选词/短文/教材语篇填空的子题没有选项；"
                 "⑥补全对话正文每个空位必须带题号、选项恰好 A–G；⑦完成句子一词一空（答案几个词就几组下划线）；"
                 "⑧阅读/任务型/读写综合保留 A/B/C/D 篇章标号，材料段落用换行分隔。")
    lines.append("- 没见过的题型照原卷版式录（分行、编号、留空、表格、小标题都不动），题型挑 27 主类里最接近的一类，"
                 "不按想象拆题、合并、补题号或改缩进。同一张图/同一段材料配多道小题合成一个 group，"
                 "不要拆成一小题一个单元。")
    chosen = data.get("rules") or []
    if limit and limit > 0:
        chosen = chosen[:limit]
    for rule in chosen:
        words = "/".join(rule.get("any") or [])
        target = rule.get("type") or ""
        if rule.get("subtype"):
            target += "/" + str(rule["subtype"])
        kind = rule.get("kind") or ""
        conditions = ""
        if rule.get("none"):
            conditions += "；排除 " + "/".join(rule["none"])
        if rule.get("subtype_any"):
            conditions += "；同时含 " + "/".join(rule["subtype_any"])
        lines.append(f"- 含 {words}{conditions} → {target}（{kind}）")
    for item in data.get("containers") or []:
        lines.append(f"- {item.get('name')}：{item.get('note')}")
    for item in data.get("lesson_constraints") or []:
        text = str(item.get("text") or "").strip()
        if text:
            lines.append("- " + text)
    return "\n".join(lines)


def audit_units(units, raw_text: str = "", path: str | Path | None = None) -> list[dict]:
    """把结构单元和本口径对一遍，只报差异，不改任何认定。

    结构单元（structure_ai 产出）带 type/unit_kind/source_span；按窗口起点所属的
    大题标题反查题型与组/单，并提示越界和题前材料缺口，差异保留原文坐标。
    仅供留痕与提示，结构闸门仍以原卷证据为准。
    """
    if not looks_like_sync_practice(raw_text):
        return []
    lines = str(raw_text or "").splitlines()
    headings = heading_candidates(raw_text, path)
    findings: list[dict] = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        span = unit.get("source_span") or {}
        start, end = span.get("start_line"), span.get("end_line")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            continue
        owners = [row for row in headings if row['start_line'] <= start <= row['end_line']]
        if not owners:
            continue
        owner = owners[-1]
        hit = classify(owner['title'], path)
        if not hit:
            continue
        problems = []
        if end > owner['end_line']:
            problems.append('来源窗口越过本大题边界，可能串入下一大题')
        if unit.get('unit_kind') == 'group' and start > owner['start_line']:
            prefix = lines[owner['start_line']:start - 1]
            # 只提示已知大题标题与题组起点间的原文缺口，不伪称已核验材料。
            if any(plain_text(line).strip() or '[IMG ' in line for line in prefix):
                problems.append('来源窗口起点之前存在同大题原文，需核对共享材料/表格/导语是否遗漏')
        if hit["type"] and unit.get("type") and hit["type"] != unit["type"]:
            problems.append(f"题型 应为 {hit['type']}，实为 {unit['type']}")
        if hit["kind"] and unit.get("unit_kind") and hit["kind"] != unit["unit_kind"]:
            problems.append(f"组/单 应为 {hit['kind']}，实为 {unit['unit_kind']}")
        if problems:
            findings.append({"numbers": unit.get("numbers") or [], "title": hit["title"],
                             "heading_line": owner['start_line'], 'source_span': dict(span),
                             "rule": hit["rule"], "problems": problems})
    return findings


# 原卷提示标记：真题来源（[2025·六安月考]、[2023·安徽]）与命题特征（[新考法·…]、[情境题]）。
# 这些标记是原卷原文的一部分，必须随题干/材料进入成品，不能被清洗掉。
_MARK_HINT = re.compile(
    r"(?:19|20)\d{2}|新考法|情境|素养|跨学科|改编|原创|月考|期中|期末|模拟|真题|中考|高考|开学|调研|质检|联考"
    r"|传统文化|安徽人文|榜样人物|经典谚语|熟词生义")
_MARK_SKIP = re.compile(
    r"^(?:(?:IMG|TABLE|/TABLE)(?:\s|$)|解析|答案|详解|分析|考点|点睛|点评|范文|思路|参考|文章大意|导语|备注)", re.I)


def source_marks(text: str) -> list[str]:
    """按原文取出方括号提示标记（排除图片/表格/答案解析类标签）。"""
    raw = str(text or "")
    found: list[str] = []
    for match in re.finditer(r"[\[【]([^\[\]【】]{1,80})[\]】]", raw):
        body = plain_text(match.group(1)).strip()
        if not body or _MARK_SKIP.match(body) or not _MARK_HINT.search(body):
            continue
        found.append(plain_text(match.group(0)).strip())
    return found


def _mark_key(text: str) -> str:
    return re.sub(r"\s+", "", plain_text(text or ""))


def _own_text(unit: dict) -> str:
    # Only delivered question fields count: source_section/answers/explanations do not.
    parts = [str(unit.get("stem") or ""), str(unit.get("material") or "")]
    options = unit.get("options") or []
    parts.extend(str(item) for item in (options.values() if isinstance(options, dict) else options))
    # Tables are delivered content too; read only their explicit text/items fields.
    for block in unit.get("materialBlocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("text"):
            parts.append(str(block["text"]))
        for row in block.get("rows") or []:
            cells = row.get("cells") or [] if isinstance(row, dict) else row
            for cell in cells or []:
                if isinstance(cell, dict):
                    parts.append(str(cell.get("text") or ""))
                    parts.extend(str(item) for item in cell.get("items") or [])
    return "\n".join(parts)


def _children(unit: dict) -> list[dict]:
    return [c for key in ("children", "subquestions") for c in unit.get(key) or [] if isinstance(c, dict)]


def _unit_text(unit: dict) -> str:
    return "\n".join([_own_text(unit)] + [_own_text(c) for c in _children(unit)])


def _keeps(mark: str, text: str) -> bool:
    # Full bracket and body must survive; a matching year or region is never enough.
    return _mark_key(mark) in {_mark_key(item) for item in source_marks(text)}


def _numbers(unit: dict) -> list:
    return unit.get("numbers") or ([unit["number"]] if unit.get("number") is not None else
                                   [c.get("number") for c in _children(unit) if c.get("number") is not None])


def _valid_span(span, lines):
    if not isinstance(span, dict):
        return False
    start, end = span.get("start_line"), span.get("end_line")
    return type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)


def _scoped_mark_window(unit, lines, headings):
    """Resolve from explicit source identity, never by a global printed-number fallback."""
    if (unit.get("identity_version") != "scoped-v1" or not unit.get("section_id")
            or not (unit.get("unit_id") or unit.get("id"))):
        return None
    section = unit.get("source_section") or {}
    owners = [h for h in headings if h["start_line"] == section.get("start_line")
              and _mark_key(h["title"]) == _mark_key(section.get("title") or "")]
    if len(owners) != 1:
        return None
    owner = owners[0]
    if any(key in section and section[key] != owner.get(key)
           for key in ("end_line", "context", "occurrence")):
        return None
    nums, qids = _numbers(unit), unit.get("question_ids") or {}
    if not nums or any(not isinstance(qids.get(str(n)), str) or not qids[str(n)] for n in nums):
        return None
    span = unit.get("source_span") or {}
    if span:
        if (not _valid_span(span, lines)
                or span.get("basis") not in {"ai_classified", "cleaned_source"}):
            return None
        start, end = span["start_line"], span["end_line"]
        if owner["start_line"] <= start <= end <= owner["end_line"]:
            return start, end, owner
        return None
    # Ordinary final items carry source_section + question_ids, not source_span.
    # Reconstruct only when every requested printed number occurs once in this section.
    from .question_identity import printed_anchors
    local = lines[owner["start_line"] - 1:owner["end_line"]]
    anchors = printed_anchors(local)
    if any(sum(n == wanted for _, n in anchors) != 1 for wanted in nums):
        return None
    positions = [i for i, n in anchors if n in nums]
    start = min(positions)
    end = next((i - 1 for i, n in anchors if i > max(positions) and n not in nums), len(local) - 1)
    if unit.get("unit_kind") == "group":
        # A later group may have different printed numbers but an earlier material
        # boundary. Without its explicit span, do not borrow the whole section.
        if any(n not in nums for _, n in anchors):
            return None
        start = 0
    return owner["start_line"] + start, owner["start_line"] + end, owner


def _mark_requirements(unit, lines, window):
    start, end, owner = window
    requirements = [(m, str(unit.get("stem") or ""), "大题作答指令")
                    for m in source_marks(lines[owner["start_line"] - 1])]
    from .question_identity import numbered_projection, printed_anchors
    projected = numbered_projection(lines[start - 1:end])
    anchors = printed_anchors(projected)
    children = _children(unit)
    # Blanks embedded in continuous cloze/dialogue prose belong to the shared material.
    material_type = (unit.get("question_type") or unit.get("type")) in {
        "完形填空", "选词填空", "短文填空", "教材语篇填空", "补全对话", "语法选择"}
    for index, line in enumerate(projected):
        if start + index == owner["start_line"] and index == 0:
            continue
        previous = [(i, n) for i, n in anchors if i <= index]
        number = previous[-1][1] if previous else None
        matches = [c for c in children if str(c.get("number")) == str(number)]
        unique = number is not None and sum(n == number for _, n in anchors) == 1
        target, label = _own_text(unit), "所属单元题干/材料"
        # Cloze/dialogue prose contains inline blank numbers; these are material.
        # Printed answer-option blocks still belong to their own child.
        option_block = False
        if previous:
            anchor_index = previous[-1][0]
            next_index = next((i for i, _ in anchors if i > anchor_index), len(projected))
            option_block = any(re.search(r"(?:^|\d[.．、]\s*|\s)A[.．、]", item)
                               for item in projected[anchor_index:next_index])
        if material_type and option_block and number is not None:
            option_anchors = []
            for ai, (pos, n) in enumerate(anchors):
                stop = anchors[ai + 1][0] if ai + 1 < len(anchors) else len(projected)
                if n == number and any(re.search(r"(?:^|\d[.．、]\s*|\s)A[.．、]", item)
                                       for item in projected[pos:stop]):
                    option_anchors.append(pos)
            unique = len(option_anchors) == 1
        if unique and len(matches) == 1 and (not material_type or option_block):
            target, label = _own_text(matches[0]), f"所属子题{number}题干"
        elif material_type:
            target, label = _own_text(unit), "原材料/作答指令"
        for mark in source_marks(line):
            requirements.append((mark, target, label))
    return requirements


def audit_source_marks(units, raw_text: str = "", path: str | Path | None = None) -> list[dict]:
    """同步来源窗口留痕；可靠 scoped 身份才验证所属题，不修改题目或闸门。"""
    if not looks_like_sync_practice(raw_text):
        return []
    lines = str(raw_text or "").splitlines()
    headings = heading_candidates(raw_text, path)
    findings: list[dict] = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        span = unit.get("source_span") or {}
        window = _scoped_mark_window(unit, lines, headings)
        if window:
            requirements = _mark_requirements(unit, lines, window)
        elif unit.get("identity_version") != "scoped-v1" and _valid_span(span, lines):
            requirements = [(m, _unit_text(unit), "题干/材料")
                            for m in source_marks("\n".join(lines[span["start_line"] - 1:span["end_line"]]))]
        else:
            continue
        missing = [(mark, label) for mark, text, label in requirements if not _keeps(mark, text)]
        if missing:
            marks = list(dict.fromkeys(mark for mark, _ in missing))
            findings.append({"unit_id": unit.get("unit_id") or unit.get("id") or "",
                             "numbers": _numbers(unit), "source_span": dict(span), "marks": marks,
                             "problems": ["原卷提示标记未进入题干/材料：" + " ".join(marks)] +
                                         [f"{mark} 应保留于{label}" for mark, label in missing]})
    return findings


def audit_batch_source_marks(units, raw_text: str) -> list[dict]:
    """Read-only final check. Legacy/unbound batches check exact marks globally only."""
    rows = [u for u in units or [] if isinstance(u, dict)]
    findings = audit_source_marks(rows, raw_text)
    lines = str(raw_text or "").splitlines()
    headings = heading_candidates(raw_text) if looks_like_sync_practice(raw_text) else []
    if rows and all(_scoped_mark_window(u, lines, headings) for u in rows):
        return findings
    corpus = "\n".join(_unit_text(u) for u in rows)
    missing = list(dict.fromkeys(m for m in source_marks(raw_text) if not _keeps(m, corpus)))
    if missing:
        findings.append({"marks": missing, "problems": ["原卷提示标记未完整保留：" + " ".join(missing)]})
    return findings


def validate(path: str | Path | None = None) -> list[str]:
    """自检：题型/子类必须存在于 filter-framework.json，kind 只能是两种取值。"""
    framework = json.loads((ROOT / "references" / "filter-framework.json").read_text(encoding="utf-8-sig"))
    legal = {item["name"]: set(item.get("subtypes") or []) for item in framework.get("question_types") or []}
    problems: list[str] = []
    for rule in rules(path):
        name, subtype, kind = rule.get("type"), rule.get("subtype"), rule.get("kind")
        if name not in legal:
            problems.append(f"{rule.get('id')}: 未知主类 {name!r}")
        elif subtype and subtype not in legal[name]:
            problems.append(f"{rule.get('id')}: 主类 {name} 无子类 {subtype!r}")
        if kind not in ("group", "standalone"):
            problems.append(f"{rule.get('id')}: kind 只能是 group/standalone，实为 {kind!r}")
        if not rule.get("any"):
            problems.append(f"{rule.get('id')}: 缺少 any 关键词")
    for name, kind in type_kind(path).items():
        if name not in legal:
            problems.append(f"type_kind: 未知主类 {name!r}")
        if kind not in ("group", "standalone"):
            problems.append(f"type_kind[{name}]: 非法 kind {kind!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = [a for a in (argv if argv is not None else sys.argv[1:]) if a.strip()]
    if not args:
        problems = validate()
        print("规则自检:", "通过" if not problems else "失败")
        for line in problems:
            print(" -", line)
        print("主类数:", len(type_kind()), "规则数:", len(rules()))
        return 0 if not problems else 1
    bad = 0
    for title in args:
        hit = classify(title)
        if hit is None:
            print(f"未匹配\t{normalize(title)}")
            bad += 1
        else:
            print(f"{hit['type']}\t{hit['subtype'] or '-'}\t{hit['kind']}\t{hit['title']}\t({hit['rule']})")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
