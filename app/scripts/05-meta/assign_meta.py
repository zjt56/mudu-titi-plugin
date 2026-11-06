#!/usr/bin/env python3
"""保留模型判断的难度，并为批次补充分类（filterMetadata.categories）。

分类规则来源：references/difficulty-category.json。难度未提供时留空。
用法：assign_meta.py --batch <batch.json> --out <batch_meta.json> [--report]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "references" / "difficulty-category.json"


def _norm(kp: str) -> str:
    # 旧目录名「阅读技能/X」演进为「设题角度/阅读/X」，同一含义。
    if kp.startswith("阅读技能/"):
        return "设题角度/阅读/" + kp[len("阅读技能/"):]
    return kp


def _word_stats(text: str):
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", text)
    n = len(words)
    if n == 0:
        return 0, 0.0, 0.0
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    avg_len = n / sentences
    long_ratio = sum(1 for w in words if len(w) >= 7) / n
    return n, avg_len, long_ratio


def _option_homogeneous(options):
    if not options or len(options) < 2:
        return False
    def _text(o):
        if isinstance(o, dict):
            return str(o.get("text", ""))
        return str(o or "")
    lens = [_text(o) and len(_text(o)) or 0 for o in options]
    return max(lens) - min(lens) <= 2 and all(l > 0 for l in lens)


def _all_knowledge(item):
    kps = set(_norm(k) for k in item.get("knowledge", []) if isinstance(k, str))
    for sq in item.get("subquestions", []) or []:
        kps.update(_norm(k) for k in sq.get("knowledge", []) if isinstance(k, str))
    return kps


def _score(item, fs):
    text = f"{item.get('stem', '')} {item.get('material', '')}"
    n, avg, longr = _word_stats(text)
    homog = _option_homogeneous(item.get("options", []))
    score = fs["start"]
    basis = [f"起始 {fs['start']}"]
    for rule in fs.get("rules", []):
        f = rule["feature"]
        ok = False
        if f == "word_count":
            ok = ("lt" not in rule or n < rule["lt"]) and ("gte" not in rule or n >= rule["gte"])
        elif f == "avg_sentence_len":
            ok = "gte" not in rule or avg >= rule["gte"]
        elif f == "long_word_ratio":
            ok = "gte" not in rule or longr >= rule["gte"]
        elif f == "option_homogeneous":
            ok = homog == rule.get("eq", True)
        if ok:
            score += rule["delta"]
            basis.append(f"{rule['basis']} ({rule['delta']:+.2f})")
    kps = _all_knowledge(item)
    for rule in fs.get("knowledge_rules", []):
        if any(_norm(k) in kps for k in rule["when_knowledge_any"]):
            score += rule["delta"]
            basis.append(f"{rule['basis']} ({rule['delta']:+.2f})")
    score = max(0.0, min(1.0, score))
    if n == 0 and not item.get("subquestions"):
        return None, None, ["无文字特征，留空待编辑"]
    if score >= 0.8:
        diff = "容易"
    elif score >= 0.5:
        diff = "适中"
    else:
        diff = "困难"
    return diff, round(score, 3), basis


def _categories(item, rules):
    kps = _all_knowledge(item)
    typ = item.get("question_type", "")
    out = []
    for name, rule in rules.get("categories", {}).items():
        hit = False
        if "when_types" in rule and typ in rule["when_types"]:
            hit = True
        if not hit and "when_knowledge_any" in rule:
            if any(_norm(k) in kps for k in rule["when_knowledge_any"]):
                if name == "易错题" and rule.get("when_option_homogeneous"):
                    hit = _option_homogeneous(item.get("options", [])) and typ in rule.get("when_option_homogeneous_types", [typ])
                else:
                    hit = True
        if hit and name not in out:
            out.append(name)
    # 没有命中可靠分类时明确占位，不凭空归入某一类，也不阻断后续审核。
    return out or ["未提供"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    if not args.batch.is_file():
        print("未找到批次组装文件，等待组装完成")
        return 1
    data = json.loads(args.batch.read_text(encoding="utf-8"))
    items = data.get("items", [])
    pending = []
    for it in items:
        fm = it.get("filterMetadata") or {}
        # 难度由模型根据题面主观判断；不以长度等机械特征代替模型结论。
        # 模型漏判时用中性默认值补齐，避免发布闸门 A3 反复阻断整批。
        judged = it.get("difficulty") if isinstance(it.get("difficulty"), str) else fm.get("difficulty")
        if judged not in ("容易", "适中", "困难"):
            judged = "适中"
        fm["difficulty"] = judged
        fm["difficultyValue"] = None
        diff = judged
        fm["categories"] = _categories(it, rules)
        it["filterMetadata"] = fm
        if diff is None:
            pending.append(it.get("id"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.report:
        dist = Counter(x["filterMetadata"].get("difficulty") for x in items)
        cats = Counter(tuple(x["filterMetadata"].get("categories")) for x in items)
        print(f"难度分布: {dict(dist)}")
        print(f"分类分布: { {k: v for k, v in cats.items()} }")
        print(f"待编辑(特征不足): {pending}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
