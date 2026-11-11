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


def plain_text(text: str) -> str:
    """只为识别去掉原生提取的字符格式；不改源文件或来源坐标。"""
    value = re.sub(r"\[IMG\s+[^\]]*\]", "", str(text or ""))
    return re.sub(r"</?(?:u|sup|sub|b|strong|i|em)\b[^>]*>", "", value).replace("**", "")


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


_LESSON = re.compile(r"^\s*第\s*[一二三四五六七八九十百\d]+\s*课时(?:\s|$)")
_UNIT = re.compile(r"^\s*(?:Starter\s+)?Unit\s+\d+\b", re.I)
_ROMAN_HEADING = re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVX]+\s*[.．、]", re.I)
_ITEM_LINE = re.compile(r"^\s*(?:[（(]\s*[）)]\s*)?\d+\s*[.．、)]")


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


def heading_candidates(raw_text: str, path: str | Path | None = None) -> list[dict]:
    """原文坐标上的大题 occurrence 候选；不生成/改写正式题号或确认单元。"""
    lines = str(raw_text or "").splitlines()
    rows, context, table_depth = [], "", 0
    for index, raw in enumerate(lines, 1):
        plain = plain_text(raw).strip()
        if plain.startswith("[TABLE"):
            table_depth += 1
        if table_depth:
            if plain.startswith("[/TABLE"):
                table_depth -= 1
            continue
        if _LESSON.match(plain) or _UNIT.match(plain) or plain in ("综合阅读提升", "单元话题写作", "Pronunciation"):
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
            if _LESSON.match(plain) or _UNIT.match(plain) or plain in ("综合阅读提升", "单元话题写作", "Pronunciation"):
                end = cursor
                break
        row["end_line"] = max(row["start_line"], end)
    return rows


def policy_fingerprint() -> str:
    """规则或识别实现变化后，不能复用旧结构 AI 缓存。"""
    return hashlib.sha256(RULES_PATH.read_bytes() + Path(__file__).read_bytes()).hexdigest()


def prompt_block(path: str | Path | None = None, limit: int = 0) -> str:
    """给模型看的紧凑版判定口径（含同步练习册里真实出现过的全部表述）。"""
    data = load(path)
    lines = ["同步练习/课时同步判定口径（仅当本卷是按课时编排的同步练习册时适用）："]
    lines.append("- 共用一段语篇/对话/表格/方框/图片的整题记 group；每小句独立成题的记 standalone。")
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
