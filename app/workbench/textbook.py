"""教材目录（课时同步练习）查询与校验。

目录来源：`references/textbook-catalog-1.0.md` → `references/textbook-catalog.json`。
只做精确匹配：版本、册次、单元、Section 名称必须与目录完全一致，不做模糊猜测。
试卷不要求教材匹配；只有课时同步练习类来源才使用本目录。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "references" / "textbook-catalog.json"
SOURCE_PATH = ROOT / "references" / "textbook-catalog-1.0.md"
SYNC_SOURCES = frozenset(("同步练习", "单元测验", "课本原创"))
UNIT_LEVEL_CONTAINERS = frozenset((
    "单元话题写作", "话题写作", "综合阅读提升", "阅读提升",
    "Reading Plus", "单元测验", "单元测试", "单元检测",
    "单元复习", "阶段复习", "重难点突破",
))
_UNIT_LEVEL_KEYS = tuple(_k.lower() for _k in UNIT_LEVEL_CONTAINERS)
_GRADE_DIGIT = {"七": "7", "八": "8", "九": "9"}
_cache = {"mtime": None, "data": None}


def book_grade(book_name: str) -> str:
    """将本地册次名转为远端章节目录的完整册次年级（如 '人教版七年级上册' → '7年级上册'）。"""
    text = str(book_name or "")
    for cn, digit in _GRADE_DIGIT.items():
        for term in ("上册", "下册"):
            if f"{cn}年级{term}" in text or f"{digit}年级{term}" in text:
                return f"{digit}年级{term}"
    return ""


_UPPER_TERM_RE = re.compile(r"[七八九789](?:年级)?上")
_LOWER_TERM_RE = re.compile(r"[七八九789](?:年级)?下")
_AUTUMN_RE = re.compile(r"(?:\d{2}\s*年?\s*秋(?!天|游|风|收)|秋(?:季|学期)|[（(【\[]秋[）)】\]])")
_SPRING_RE = re.compile(r"(?:\d{2}\s*年?\s*春(?!节|天|游|风|晚|秋)|春(?:季|学期)|[（(【\[]春[）)】\]])")


def season_semester(text: str) -> str:
    """将春/秋季节标记转为远端筛选学期（秋 → 上学期，春 → 下学期；规避长春、春秋等普通词）。"""
    s = str(text or "")
    has_autumn = bool(_AUTUMN_RE.search(s))
    has_spring = bool(_SPRING_RE.search(s))
    if has_autumn and not has_spring:
        return "上学期"
    if has_spring and not has_autumn:
        return "下学期"
    return ""


def book_semester(book_name: str) -> str:
    """将册次名、教材路径或季节标识转为远端筛选学期（上册/七上/秋 → 上学期，下册/七下/春 → 下学期）。"""
    text = str(book_name or "")
    has_upper = "上册" in text or bool(_UPPER_TERM_RE.search(text)) or bool(_AUTUMN_RE.search(text))
    has_lower = "下册" in text or bool(_LOWER_TERM_RE.search(text)) or bool(_SPRING_RE.search(text))
    if has_upper and not has_lower:
        return "上学期"
    if has_lower and not has_upper:
        return "下学期"
    return ""


def curriculum_path(placement: dict) -> str:
    """生成远端规范教材位置路径：有课时到课时，无课时直接挂到单元。"""
    grade = book_grade(placement.get("book", ""))
    unit = str(placement.get("unit") or "").strip()
    section = str(placement.get("section") or "").strip()
    parts = [p for p in (grade, unit, section) if p]
    return " · ".join(parts)


def is_unit_level_context(context) -> bool:
    """判断所在版块是否为与课时平级、但不属于具体课时的单元级版块。"""
    if not isinstance(context, str):
        return False
    text = re.sub(r"\*+", "", context).strip()
    if not text:
        return False
    low = re.sub(r"\s+", " ", text).lower()
    return any(key in low for key in _UNIT_LEVEL_KEYS)


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


def validate_placement(value, *, context: str | None = None) -> dict:
    """校验教材位置候选；同步练习知识点流程与独立工具共用此口径。"""
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
    section = value.get("section")
    if section is not None and not isinstance(section, str):
        raise ValueError("textbook placement section must be a string")
    section = section.strip() if isinstance(section, str) else ""
    if section and is_unit_level_context(context):
        raise ValueError(f"单元级非课时版块（{context.strip()}）应直接挂在单元下，不得填写具体课时 section：{section}")
    placement = resolve(value["version"], value["book"], value["unit"], section or None)
    if "grade" in value and value["grade"] != placement["grade"]:
        raise ValueError("textbook placement grade differs from the catalog")
    if value.get("topic") and value["topic"] != placement["topic"]:
        raise ValueError("textbook placement topic differs from the catalog")
    placement["evidence"] = list(evidence)
    return placement


def compact_index(include_sections: bool = False, *, version: str | None = None,
                  grade: str | None = None) -> dict:
    """给模型的最小目录：版本 → 册次 → 单元（可选 Section）。"""
    out = []
    for entry in catalog()["catalog"]:
        if version and entry["version"] != version and entry.get("version_label") != version:
            continue
        if grade and entry["grade"] != grade and book_grade(entry["book"]) != grade:
            continue
        row = {"version": entry["version"], "book": entry["book"], "grade": entry["grade"], "units": []}
        for unit in entry["units"]:
            item = {"unit": unit["unit"], "topic": unit.get("topic", "")}
            if include_sections:
                item["sections"] = [section["name"] for section in unit["sections"]]
            row["units"].append(item)
        out.append(row)
    return {"versions": versions(), "books": out,
            "unit_level_containers": sorted(UNIT_LEVEL_CONTAINERS),
            "note": "只用于课时同步练习来源；名称必须逐字一致，不得改写或猜测。单元话题写作、综合阅读提升等非具体课时版块请省略 section 直接挂到 unit；原卷无教材线索时留空。"}


def needs_matching(source_type) -> bool:
    return isinstance(source_type, str) and source_type in SYNC_SOURCES


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM_CN = {v: k for k, v in _CN_NUM.items()}
_DIGIT_GRADE_CN = {"7": "七", "8": "八", "9": "九", "七": "七", "八": "八", "九": "九"}


def _norm_key(text: str) -> str:
    s = str(text or "").replace("’", "'").replace("‘", "'").replace("⁃", "-")
    s = re.sub(r"\*+", "", s)
    s = re.sub(r"[\s\u3000,，.。!！?？:：;；()（）&＆/]+", "", s)
    return s.lower()


def _detect_version(texts: list[str]) -> str | None:
    hits = set()
    for text in texts:
        s = str(text or "")
        if "人教" in s:
            hits.add("人教版")
        if "外研" in s:
            hits.add("外研版")
        if "译林" in s:
            hits.add("译林版")
    return next(iter(hits)) if len(hits) == 1 else None


def _detect_grade_term(texts: list[str]) -> tuple[str, str] | None:
    for text in texts:
        s = str(text or "")
        m = re.search(r"([七八九789])(?:年级)?([上下])(?:册)?", s)
        if m:
            return _DIGIT_GRADE_CN[m.group(1)], f"{m.group(2)}册"
    return None


def _match_unit(book_entry: dict, texts: list[str]) -> dict | None:
    units_list = book_entry.get("units") or []
    # 1. Exact normalized match on full unit title
    for text in texts:
        nk = _norm_key(text)
        if not nk:
            continue
        exact = [u for u in units_list if _norm_key(u["unit"]) == nk]
        if len(exact) == 1:
            return exact[0]
    # 2. Substring match on full unit title inside a longer header/title line
    for text in texts:
        nk = _norm_key(text)
        if not nk:
            continue
        contained = [u for u in units_list if _norm_key(u["unit"]) in nk]
        if len(contained) == 1:
            return contained[0]
    # 3. Unique Starter Unit N / Unit N prefix match (Starter Unit never confused with Unit)
    for text in texts:
        s = re.sub(r"\*+", "", str(text or "")).strip()
        m = re.search(r"\b(Starter\s+Unit\s+\d+|Starter\s+Unit|Unit\s+\d+)\b", s, re.I)
        if not m:
            continue
        prefix = re.sub(r"\s+", " ", m.group(1)).strip().lower()
        is_starter = prefix.startswith("starter")
        matched = []
        for u in units_list:
            uname = re.sub(r"\s+", " ", u["unit"]).strip().lower()
            if is_starter and uname.startswith(prefix):
                matched.append(u)
            elif not is_starter and not uname.startswith("starter") and (
                uname == prefix or uname.startswith(prefix + " ") or re.match(re.escape(prefix) + r"(?!\d)", uname)
            ):
                matched.append(u)
        if len(matched) == 1:
            return matched[0]
    return None


def _match_section(unit_entry: dict, texts: list[str]) -> str | None:
    sections = unit_entry.get("sections") or []
    if not sections:
        return None
    # 1. Exact normalized match
    for text in texts:
        nk = _norm_key(text)
        if not nk:
            continue
        exact = [s["name"] for s in sections if _norm_key(s["name"]) == nk]
        if len(exact) == 1:
            return exact[0]
    # 2. Unique full section-name substring match inside a longer header line
    for text in texts:
        nk = _norm_key(text)
        if not nk:
            continue
        contained = [s["name"] for s in sections if _norm_key(s["name"]) and _norm_key(s["name"]) in nk]
        if len(contained) == 1:
            return contained[0]
    # 3. Unique period number match (第一课时 / 第2课时 / 课时3)
    for text in texts:
        s = re.sub(r"\*+", "", str(text or ""))
        m = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*课时|课时\s*(\d+)", s)
        if not m:
            continue
        raw = m.group(1) or m.group(2)
        num = int(raw) if raw.isdigit() else _CN_NUM.get(raw)
        if num not in _NUM_CN:
            continue
        prefix = f"第{_NUM_CN[num]}课时"
        matched = [sec["name"] for sec in sections if sec["name"].startswith(prefix)]
        if len(matched) == 1:
            return matched[0]
        # 兼容远端目录个别单元首课时未写「第一课时」前缀（如八上 Unit 1 的 Section A (1a-1d)、译林九上 Unit 7 的 Welcome to the unit）
        if (not matched and num == 1 and len(sections) >= 2
                and not sections[0]["name"].startswith("第")
                and sections[1]["name"].startswith("第二课时")):
            return sections[0]["name"]
    # 4. Unique suffix match after stripping 「第X课时」 (e.g. header only writes Pronunciation or Section A (1a-2e))
    stripped = []
    for sec in sections:
        tail = re.sub(r"^第[一二三四五六七八九十\d]+课时\s*", "", sec["name"]).strip()
        tk = _norm_key(tail)
        if tk:
            stripped.append((sec["name"], tk))
    for text in texts:
        nk = _norm_key(text)
        if not nk:
            continue
        matched = [name for name, tk in stripped if tk == nk or (len(tk) >= 6 and tk in nk)]
        if len(matched) == 1:
            return matched[0]
    return None


def infer_placement(batch_dir, unit: dict | None = None) -> dict | None:
    """基于原卷卷头与已识别元数据确定性匹配教材位置；无充分证据时返回 None（留空放行）。"""
    root = Path(batch_dir)
    def _load(rel):
        p = root / rel
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    manifest = _load("workflow/manifest.json") or _load("manifest.json")
    mmeta = manifest.get("meta") if isinstance(manifest.get("meta"), dict) else {}
    meta = _load("01_source/metadata.json")
    conf = _load("01_source/structure.confirmed.json")
    cmeta = conf.get("meta") if isinstance(conf.get("meta"), dict) else {}

    def _known(v):
        return isinstance(v, str) and v.strip() and v.strip() not in ("未提供", "未填写")

    source_type = next((src["source_type"].strip() for src in (mmeta, meta, cmeta) if _known(src.get("source_type"))), "")
    if not needs_matching(source_type):
        return None

    body_path = root / "01_source/正文.txt"
    header_lines: list[tuple[int, str]] = []
    if body_path.is_file():
        for idx, raw in enumerate(body_path.read_text(encoding="utf-8-sig").splitlines()[:10], 1):
            cleaned = re.sub(r"\*+", "", raw).strip()
            if cleaned:
                header_lines.append((idx, cleaned))
            if len(header_lines) >= 6:
                break

    sec_meta = unit.get("source_section") if isinstance(unit, dict) and isinstance(unit.get("source_section"), dict) else {}
    unit_ctx = str(sec_meta.get("context") or "").strip()
    unit_title = str(sec_meta.get("title") or "").strip()
    header_texts = [line for _, line in header_lines]

    version = _detect_version([
        mmeta.get("textbook_version"), mmeta.get("version"),
        meta.get("textbook_version"), meta.get("version"),
        *header_texts, meta.get("paper_title"), meta.get("source"),
    ])
    if not version:
        return None

    gt = _detect_grade_term([
        mmeta.get("book"), meta.get("book"),
        *header_texts, meta.get("paper_title"), meta.get("source"),
    ])
    if not gt:
        return None
    cn_grade, term = gt
    book_candidates = [b for b in books(version) if f"{cn_grade}年级{term}" in b["book"]]
    if len(book_candidates) != 1:
        return None
    book_entry = book_candidates[0]

    unit_entry = _match_unit(book_entry, [
        *header_texts, unit_ctx, meta.get("unit"), meta.get("paper_title"), meta.get("source"),
    ])
    if unit_entry is None:
        return None

    # 单元级非课时版块（单元话题写作、综合阅读提升、Reading Plus、单元测验等）直接挂到单元，不填 section
    unit_level_hit = (
        source_type == "单元测验"
        or is_unit_level_context(unit_ctx)
        or is_unit_level_context(unit_title)
        or any(is_unit_level_context(line) for line in header_texts[:4])
        or is_unit_level_context(meta.get("paper_title"))
        or is_unit_level_context(meta.get("source"))
    )
    section = None
    if not unit_level_hit:
        section = _match_section(unit_entry, [unit_ctx, *header_texts[:4], meta.get("lesson")])

    if header_lines:
        span = f"L{header_lines[0][0]}-L{header_lines[-1][0]}" if len(header_lines) > 1 else f"L{header_lines[0][0]}"
        snippet = " / ".join(line for _, line in header_lines[:3])
        evidence = [f"正文 {span}：{snippet}"]
    else:
        title_ev = str(meta.get("paper_title") or meta.get("source") or "").strip()
        if not title_ev:
            return None
        evidence = [f"metadata.json：{title_ev}"]

    candidate = {
        "version": version,
        "book": book_entry["book"],
        "unit": unit_entry["unit"],
        "evidence": evidence,
    }
    if section:
        candidate["section"] = section
    try:
        return validate_placement(candidate, context=unit_ctx or None)
    except ValueError:
        return None


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
