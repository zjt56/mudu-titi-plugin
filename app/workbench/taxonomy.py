# -*- coding: utf-8 -*-
"""题库标签目录：27 个固定主类、子类、来源、年份、地区、难度、分类、知识点。

工作台只做“读目录 + 校验 + 给候选”，不新增/改名任何目录项。
标签目录来自 references/filter-framework.json、references/knowledge-catalog.json，
题型维度来自 references/type-knowledge-map.json 的 by_type.dims。
这些固定文件按 mtime 缓存，修改后会重新加载。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK_PATH = ROOT / "references" / "filter-framework.json"
KNOWLEDGE_PATH = ROOT / "references" / "knowledge-catalog.json"
TYPE_KNOWLEDGE_PATH = ROOT / "references" / "type-knowledge-map.json"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=4)
def _framework_cached(mtime: float):
    return _read(FRAMEWORK_PATH)


@lru_cache(maxsize=4)
def _knowledge_cached(mtime: float):
    return _read(KNOWLEDGE_PATH)


def framework():
    return _framework_cached(FRAMEWORK_PATH.stat().st_mtime)


def knowledge_points():
    return _knowledge_cached(KNOWLEDGE_PATH.stat().st_mtime).get("points", [])


def types():
    """27 个固定主类，按目录顺序。"""
    return [t.get("name", "") for t in framework().get("question_types", []) if t.get("name")]


def subtypes(main_type: str):
    for entry in framework().get("question_types", []):
        if entry.get("name") == main_type:
            return list(entry.get("subtypes") or [])
    return []


def subtype_map():
    return {t.get("name", ""): list(t.get("subtypes") or []) for t in framework().get("question_types", [])}


def difficulties():
    return [d.get("name", "") for d in framework().get("difficulties", []) if d.get("name")]


def difficulty_name(value):
    """兼容过滤元数据中的数值难度；边界 0.8/0.5 归入较容易档。"""
    if isinstance(value, str) and value in difficulties():
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for entry in framework().get("difficulties", []):
            if entry["min"] <= value <= entry["max"]:
                return entry["name"]
    return ""


def categories():
    return list(framework().get("categories") or [])


def sources():
    """来源类型（中考真题/中考模拟/…），不是试卷全名。"""
    return list(framework().get("sources") or [])


def years():
    return list(framework().get("years") or [])


def regions():
    return list(framework().get("regions") or [])


def knowledge_version():
    data = _knowledge_cached(KNOWLEDGE_PATH.stat().st_mtime)
    return {"version": data.get("version", ""), "versionId": data.get("versionId", ""),
            "count": len(data.get("points", []))}


@lru_cache(maxsize=4)
def _knowledge_index(mtime: float):
    points = _knowledge_cached(mtime).get("points", [])
    by_path = {}
    for point in points:
        path = str(point.get("path") or "").strip()
        if path:
            by_path[path] = {"id": point.get("id", ""), "name": point.get("name", ""), "path": path}
    leaves = {path for path in by_path
              if not any(other != path and other.startswith(path + "/") for other in by_path)}
    return by_path, leaves


def knowledge_index():
    """返回 (path -> {id,name,path}, 叶子路径集合)。"""
    return _knowledge_index(KNOWLEDGE_PATH.stat().st_mtime)


def knowledge_tree():
    """完整目录树；按真实目录前缀找父节点，名称里的斜线不是新层级。"""
    by_path, leaves = knowledge_index()
    nodes = {path: {**point, "leaf": path in leaves, "children": []}
             for path, point in by_path.items()}
    roots = []
    for path, node in nodes.items():
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        while parent and parent not in nodes:
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
        (nodes[parent]["children"] if parent else roots).append(node)
    return {"version": knowledge_version(), "complete": True, "total": len(nodes),
            "leaf_total": len(leaves), "items": roots}


def require_knowledge(paths):
    """工作台写入只接受目录中的完整叶子路径，不接受自由值或容器强制转换。"""
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError("knowledge 必须是目录叶子路径的字符串列表")
    resolved = resolve_knowledge(paths)
    if resolved["unknown"] or resolved["non_leaf"]:
        raise ValueError("知识点必须从目录叶子选择；目录外路径："
                         f"{resolved['unknown']}；中间分类：{resolved['non_leaf']}")
    return list(dict.fromkeys(point["path"] for point in resolved["resolved"]))


def knowledge_search(query: str = "", limit: int = 40):
    """只返回叶子：目录非叶节点不能作为知识点入库。"""
    by_path, leaves = knowledge_index()
    keyword = (query or "").strip().lower()
    result = []
    for path in sorted(leaves):
        if keyword and keyword not in path.lower():
            continue
        result.append(dict(by_path[path]))
        if len(result) >= max(1, min(limit, 200)):
            break
    return result


def resolve_knowledge(paths):
    """解析完整目录路径；保留名称中的斜线，用目录路径前缀判定叶子。"""
    by_path, leaves = knowledge_index()
    resolved, unknown, non_leaf = [], [], []
    # 不把错误的字符串当作逐字符路径列表，也不因错误的容器而抛异常。
    if paths is None:
        paths = []
    elif not isinstance(paths, list):
        return {"resolved": [], "unknown": [str(paths)], "non_leaf": []}
    for raw in paths:
        if not isinstance(raw, str):
            unknown.append(str(raw))
            continue
        path = raw.strip().strip("/")
        if not path:
            continue
        point = by_path.get(path)
        canonical_path = path
        # 兼容旧批次只保存末级分支的路径，例如
        # `基本读音/字母在单词中的基本读音` -> 当前完整路径
        # `语音/基本读音/字母在单词中的基本读音`。只有唯一叶子才迁移，
        # 同名分支保持未决，不能猜测目录归属。
        if point is None:
            candidates = sorted(candidate for candidate in leaves
                               if candidate.endswith("/" + path))
            if len(candidates) == 1:
                canonical_path = candidates[0]
                point = by_path[canonical_path]
        if point is None:
            unknown.append(path)
        elif canonical_path not in leaves:
            non_leaf.append(path)
        else:
            resolved.append(dict(point))
    return {"resolved": resolved, "unknown": unknown, "non_leaf": non_leaf}


@lru_cache(maxsize=4)
def _type_rules_cached(mtime: float):
    return _read(TYPE_KNOWLEDGE_PATH).get("by_type", {})


def type_knowledge_rules():
    """27 类的维度来源；只使用 by_type.dims，不使用旧 defaults/hints。"""
    return _type_rules_cached(TYPE_KNOWLEDGE_PATH.stat().st_mtime)


def _allowed_prefixes(main_type):
    prefixes = []
    for dim in type_knowledge_rules().get(main_type, {}).get("dims", []):
        # branch 的复合值是多个维度根，不是知识点路径。
        for branch in dim.get("branch", "").split("/"):
            prefix = branch + ("/" + dim["scope"] if dim.get("scope") else "")
            # 其他的旧规则仍写「阅读技能」；现有目录已迁至阅读设题角度。
            if prefix == "阅读技能":
                prefix = "设题角度/阅读"
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)
    return prefixes


def _under(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def _string_list(value, field, problems):
    if not isinstance(value, list):
        problems.append(f"{field} 必须是字符串列表")
        return []
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            problems.append(f"{field} 列表项必须是非空字符串：{item!r}")
        else:
            result.append(item.strip())
    return result


def _main_type(value, problems):
    if not isinstance(value, str):
        problems.append("主类必须是字符串")
        return ""
    main_type = value.strip()
    if main_type and main_type not in types():
        problems.append(f"主类「{main_type}」不在 27 个固定类型里")
    return main_type


def _check_labels(values, prefixes=None):
    if not isinstance(values, dict):
        return ["标签必须是对象"]
    problems = []
    main_type = _main_type(values.get("type", ""), problems)
    subs = _string_list(values["subtypes"], "subtypes", problems) if "subtypes" in values else []
    if subs:
        allowed = subtypes(main_type) if main_type else []
        if main_type and not allowed:
            problems.append(f"主类「{main_type}」目录里没有子类，不应填 {subs}")
        else:
            bad = [s for s in subs if allowed and s not in allowed]
            if bad:
                problems.append(f"子类 {bad} 不属于「{main_type}」，可选：{allowed}")
        if main_type and main_type != "单词拼写" and len(subs) > 1:
            problems.append(f"主类「{main_type}」子类只能单选")
    difficulty = values.get("difficulty", "")
    if not isinstance(difficulty, str):
        problems.append("难度必须是字符串")
    elif difficulty.strip() and difficulty.strip() not in difficulties():
        problems.append(f"难度「{difficulty.strip()}」不合法，只能是 {'/'.join(difficulties())}")
    cats = _string_list(values["categories"], "categories", problems) if "categories" in values else []
    from workflow.metadata_policy import MISSING
    bad_cats = [c for c in cats if c not in categories() and c != MISSING]
    if bad_cats:
        problems.append(f"分类 {bad_cats} 不合法，只能是 {'/'.join(categories())}")
    paths = _string_list(values["knowledge"], "knowledge", problems) if "knowledge" in values else []
    resolved = resolve_knowledge(paths)
    if resolved["unknown"]:
        problems.append(f"知识点路径不在目录里：{resolved['unknown']}")
    if resolved["non_leaf"]:
        problems.append(f"知识点必须是叶子，这些是中间层：{resolved['non_leaf']}")
    if main_type in types():
        allowed = _allowed_prefixes(main_type) if prefixes is None else prefixes
        bad = [path for path in paths if not any(_under(path, prefix) for prefix in allowed)]
        if bad:
            problems.append(f"「{main_type}」知识点超出允许维度/范围（{'、'.join(allowed)}）：{bad}")
    return problems


def check_labels(values: dict) -> list[str]:
    """草稿校验：可缺主类、子类、知识点；已填值须合法且符合题型维度。

    列表字段必须是字符串列表；只有单词拼写允许多个子类。不检查完整性、
    组级必带维度或子题上下文；正式批次请用 check_question_labels。
    """
    return _check_labels(values)


def _unit_dims():
    result = {}
    for main_type in types():
        allow = _allowed_prefixes(main_type)
        # 其他的体裁/主题由实际材料决定，不能凭题型强制推定。
        need = [dim for dim in ("语篇体裁", "主题") if dim in allow] if main_type != "其他" else []
        result[main_type] = {
            "allow": allow,
            "forbid": ["词汇", "语法", "设题角度"] if main_type in ("完形填空", "语法选择") else [],
            "need": need,
        }
    return result


# 保留公开常量供旧调用方读取；校验每次读取按 mtime 缓存的最新目录。
UNIT_DIMS = _unit_dims()


def check_unit_dims(main_type: str, knowledge) -> list[str]:
    """组级维度校验（含听力/阅读范围）；不代替目录叶子或完整题目校验。"""
    problems = []
    main_type = _main_type(main_type, problems)
    if not main_type:
        return problems + ["主类不能为空"]
    paths = _string_list(knowledge, "knowledge", problems)
    rule = _unit_dims().get(main_type)
    if not rule:
        return problems
    banned = [path for path in paths if any(_under(path, dim) for dim in rule["forbid"])]
    if banned:
        # 这段文案由现有工作台/测试依赖。
        problems.append(f"「{main_type}」组级不挂 {'/'.join(rule['forbid'])}：{banned}；"
                        f"这些维度请挂到对应子题上")
    outside = [path for path in paths if path not in banned and
               not any(_under(path, prefix) for prefix in rule["allow"])]
    if outside:
        problems.append(f"「{main_type}」组级知识点超出允许维度/范围（{'、'.join(rule['allow'])}）：{outside}")
    missing = [dim for dim in rule["need"] if not any(_under(path, dim) for path in paths)]
    if missing:
        problems.append(f"「{main_type}」组级缺必带维度：{'/'.join(missing)}")
    return problems


_READING_TYPES = {"阅读理解", "任务型阅读", "读写综合"}
_CLOZE_TYPES = {"完形填空", "语法选择"}


# 整篇/整个大题只做组级标签（体裁、主题、内容）的题型；子题不要求、也不承载
# 细分知识点。听力长对话/短文理解属于"一材料带多题"，与阅读同理只在组级评估；
# 其余题组（如听力短对话、单项选择）按小题分别标注。
WHOLE_UNIT_TYPES = frozenset((
    "阅读理解", "任务型阅读", "完形填空", "语法选择", "听力信息转换",
    "读写综合", "选词填空", "短文填空", "教材语篇填空",
    "听力短文理解", "听力长对话理解",
))


def whole_unit_type(main_type) -> bool:
    return str(main_type or "") in WHOLE_UNIT_TYPES


def check_question_labels(question) -> list[str]:
    """正式批次单元的完整校验，返回问题列表而不修改输入。

    支持 question_type/type、filterMetadata.subtypes/categories、可选的
    knowledgeIds/knowledge_ids。子题未声明题型时只继承题型用于规则选择，
    不继承知识点或子类；完形/语法选择子题仅挂词汇/语法，阅读子题必有
    阅读角度且不挂体裁。只校验目录与结构，不判断标签语义是否符合原题。
    """
    def validate(item, parent_type=None, location="题目"):
        if not isinstance(item, dict):
            return [f"{location}必须是对象"]
        problems = []
        type_keys = [key for key in ("question_type", "type") if key in item]
        type_value = item[type_keys[0]] if type_keys else parent_type or ""
        main_type = _main_type(type_value, problems)
        if not main_type:
            problems.append("主类不能为空")
        for key in type_keys[1:]:
            alias = _main_type(item[key], problems)
            if alias != main_type:
                problems.append("question_type 与 type 不一致")
        filters = item.get("filterMetadata", {})
        if not isinstance(filters, dict):
            problems.append("filterMetadata 必须是对象")
            filters = {}
        values = {"type": main_type}
        for key in ("difficulty", "knowledge"):
            if key in item:
                values[key] = item[key]
        for key in ("subtypes", "categories"):
            if key in filters:
                values[key] = filters[key]
            elif key in item:
                values[key] = item[key]
        subs = values.get("subtypes", [])
        # 组题的格式子类不强加到每个子题；显式类型的子题也按其目录检查。
        if (parent_type is None or type_keys) and subtypes(main_type) and not subs:
            problems.append(f"主类「{main_type}」必须填写目录子类")
        paths = values.get("knowledge", [])
        child_of_whole = parent_type is not None and whole_unit_type(parent_type)
        if not isinstance(paths, list) or (not paths and not child_of_whole):
            problems.append("knowledge 必须是非空知识点列表")
        prefixes = None
        if parent_type in _CLOZE_TYPES:
            prefixes = ["词汇", "语法"]
        elif parent_type in _READING_TYPES:
            prefixes = [p for p in _allowed_prefixes(main_type) if p != "语篇体裁"]
        problems.extend(_check_labels(values, prefixes))
        clean_paths = [p.strip() for p in paths if isinstance(p, str) and p.strip()] if isinstance(paths, list) else []
        if (parent_type in _READING_TYPES and not whole_unit_type(parent_type)
                and not any(_under(p, "设题角度/阅读") for p in clean_paths)):
            problems.append("阅读子题必须包含设题角度/阅读叶子")
        expected_ids = [point["id"] for point in resolve_knowledge(clean_paths)["resolved"]]
        for key in ("knowledgeIds", "knowledge_ids"):
            if key in item:
                ids = _string_list(item[key], key, problems)
                # IDs 是无序标签集合；同一 ID 不应重复，不能漏项或附加项。
                if len(ids) != len(set(ids)) or set(ids) != set(expected_ids):
                    problems.append(f"{key} 与 knowledge 目录叶子 ID 不一致")
        children = item.get("subquestions", [])
        if not isinstance(children, list):
            problems.append("subquestions 必须是列表")
            children = []
        is_group = bool(children) or item.get("unit_kind") == "group"
        if is_group and parent_type is None and main_type in types():
            problems.extend(check_unit_dims(main_type, clean_paths))
        for index, child in enumerate(children, 1):
            problems.extend(validate(child, main_type, f"子题[{index}]"))
        return [f"{location}：{problem}" for problem in problems]

    return validate(question)


def taxonomy():
    return {
        "version": framework().get("version", ""),
        "types": types(),
        "subtypes": subtype_map(),
        "difficulties": difficulties(),
        "categories": categories(),
        "sources": sources(),
        "years": years(),
        "regions": regions(),
        "knowledge": knowledge_version(),
    }
