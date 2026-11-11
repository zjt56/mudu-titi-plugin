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


def normalize(title: str) -> str:
    """去掉题号前缀、方括号标签与空白，得到用于匹配的标题。"""
    text = _BRACKET.sub("", str(title or ""))
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
    text = str(body or "")
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
        else:
            # 同步练习册的任务型阅读以“根据短文内容回答问题/不超过N个词”为主，
            # 没有混排标记时按 阅读问答 处理（与已上传同系列批次一致）。
            hit["subtype"] = "阅读问答"
    elif name == "补全对话":
        if re.search(r"[A-G][.．]\s*\S", text) and sum(
                1 for letter in "ABCDEFG" if re.search(rf"(?<![A-Za-z]){letter}[.．]\s*\S", text)) >= 5:
            hit["subtype"] = "7选5"
        elif "特殊疑问词" in hit.get("title", ""):
            hit["subtype"] = "填写单词"
    return hit


def classify_with_body(title: str, body: str, path: str | Path | None = None) -> dict | None:
    return refine(classify(title, path), body)


_UNIT_HINTS = ("单元卷", "单元测试", "期中", "期末", "月考", "模拟", "中考")


def looks_like_sync_practice(text: str) -> bool:
    """粗判材料是否为同步练习册（按课时编排），用于决定是否下发本口径。"""
    body = str(text or "")
    if not body:
        return False
    hints = sum(1 for hint in _SYNC_HINTS if hint in body)
    units = sum(1 for hint in _UNIT_HINTS if hint in body)
    return hints >= 2 and hints > units


def prompt_block(path: str | Path | None = None, limit: int = 0) -> str:
    """给模型看的紧凑版判定口径（含同步练习册里真实出现过的全部表述）。"""
    data = load(path)
    lines = ["同步练习/课时同步判定口径（仅当本卷是按课时编排的同步练习册时适用）："]
    lines.append("- 共用一段语篇/对话/表格/方框/图片的整题记 group；每小句独立成题的记 standalone。")
    chosen = data.get("rules") or []
    if limit and limit > 0:
        chosen = chosen[:limit]
    for rule in chosen:
        words = "/".join((rule.get("any") or [])[:2])
        target = rule.get("type") or ""
        if rule.get("subtype"):
            target += "/" + str(rule["subtype"])
        kind = rule.get("kind") or ""
        lines.append(f"- 含 {words} → {target}（{kind}）")
    for item in data.get("containers") or []:
        lines.append(f"- {item.get('name')}：{item.get('note')}")
    return "\n".join(lines)


def audit_units(units, raw_text: str = "", path: str | Path | None = None) -> list[dict]:
    """把结构单元和本口径对一遍，只报差异，不改任何认定。

    结构单元（structure_ai 产出）带 type/unit_kind/source_span；用 span 里第一条能
    命中规则的行（通常是大题标题）反查应记的题型与组/单，不一致时给出可定位的差异。
    仅供留痕与提示，结构闸门仍以原卷证据为准。
    """
    lines = str(raw_text or "").splitlines()
    findings: list[dict] = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        span = unit.get("source_span") or {}
        start, end = span.get("start_line"), span.get("end_line")
        chunk = lines[start - 1:end] if isinstance(start, int) and isinstance(end, int) and start >= 1 else []
        hit = None
        for line in chunk:
            hit = classify(line, path)
            if hit:
                break
        if not hit:
            continue
        problems = []
        if hit["type"] and unit.get("type") and hit["type"] != unit["type"]:
            problems.append(f"题型 应为 {hit['type']}，实为 {unit['type']}")
        if hit["kind"] and unit.get("unit_kind") and hit["kind"] != unit["unit_kind"]:
            problems.append(f"组/单 应为 {hit['kind']}，实为 {unit['unit_kind']}")
        if problems:
            findings.append({"numbers": unit.get("numbers") or [], "title": hit["title"],
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
