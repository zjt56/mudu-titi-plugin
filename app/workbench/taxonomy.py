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


def subtypes_required_but_missing(main_type, subs):
    """True only for the recoverable "a catalog subtype exists but none was
    given" defect. A non-catalog / wrong subtype is not this case and still
    fails full validation; an empty subtype list on a typeless question is not
    a subtype defect either."""
    if not isinstance(main_type, str) or main_type not in types():
        return False
    allowed = subtypes(main_type)
    if not allowed:
        return False
    if not isinstance(subs, list):
        return False
    return not any(isinstance(v, str) and v.strip() for v in subs)



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
    # O(N) ancestor-prefix sweep instead of O(N^2) pairwise startswith scan.
    non_leaves = set()
    for path in by_path:
        pos = path.rfind("/")
        while pos > 0:
            prefix = path[:pos]
            if prefix in by_path:
                non_leaves.add(prefix)
            pos = path.rfind("/", 0, pos)
    leaves = {path for path in by_path if path not in non_leaves}
    return by_path, leaves


def knowledge_index():
    """返回 (path -> {id,name,path}, 叶子路径集合)。"""
    return _knowledge_index(KNOWLEDGE_PATH.stat().st_mtime)


_DISALLOWED_VOCAB_TYPES = ("完成句子", "短文填空", "教材语篇填空")


def _clean_unit_kind(unit_kind) -> str | None:
    if isinstance(unit_kind, str):
        kind = unit_kind.strip()
        if kind in ("group", "standalone"):
            return kind
    return None


def _is_selectable_for_type(path: str, main_type: str | None, leaves: set[str],
                            allowed_prefixes=None, exact_paths=None,
                            excluded_prefixes=None, types_set=None,
                            unit_kind: str | None = None) -> bool:
    """判定某目录路径在指定题型下是否可选为最终知识点。"""
    valid_types = types_set if types_set is not None else _types_set()
    if not main_type or main_type not in valid_types:
        return path in leaves
    if not has_answer_point_dim(main_type, unit_kind) and (
            _under(path, "词汇/考察词汇") or _under(path, "词汇/答案考点")):
        return False
    if exact_paths is None:
        exact_paths = exact_knowledge_paths(main_type, unit_kind=unit_kind)
    if excluded_prefixes is None:
        excluded_prefixes = _excluded_prefixes(main_type, unit_kind=unit_kind)
    if exact_paths:
        return path in exact_paths and not any(
            _under(path, excluded) for excluded in excluded_prefixes)
    return path in leaves and knowledge_path_allowed(
        path, main_type, allowed_prefixes,
        excluded_prefixes=excluded_prefixes, exact_paths=exact_paths,
        unit_kind=unit_kind)


@lru_cache(maxsize=128)
def _knowledge_tree_cached(k_mtime: float, f_mtime: float, t_mtime: float,
                           clean_type: str, clean_kind: str):
    by_path, leaves = _knowledge_index(k_mtime)
    nodes = {path: {**point, "leaf": path in leaves, "selectable": path in leaves, "children": []}
             for path, point in by_path.items()}
    roots = []
    for path, node in nodes.items():
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        while parent and parent not in nodes:
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
        (nodes[parent]["children"] if parent else roots).append(node)
    k_data = _knowledge_cached(k_mtime)
    version = {"version": k_data.get("version", ""), "versionId": k_data.get("versionId", ""),
               "count": len(k_data.get("points", []))}
    valid_types = _types_set_cached(f_mtime)
    scoped = bool(clean_type and clean_type in valid_types)
    if not scoped:
        return {"version": version, "complete": True, "scoped": False,
                "type": "", "unit_kind": "", "allowed_prefixes": [], "total": len(nodes),
                "selectable_total": len(leaves), "leaf_total": len(leaves), "items": roots}
    uk = clean_kind or None
    allowed = list(_allowed_prefixes_cached(t_mtime, clean_type, uk))
    exact = _exact_paths_cached(t_mtime, clean_type, uk)
    excluded = _excluded_prefixes_cached(t_mtime, clean_type, uk)

    def prune(node):
        path = node["path"]
        if any(_under(path, ex) for ex in excluded):
            return None
        if not has_answer_point_dim(clean_type, uk) and (
                _under(path, "词汇/考察词汇") or _under(path, "词汇/答案考点")):
            return None
        if exact and path in exact:
            return {**node, "leaf": True, "selectable": True, "children": []}
        children = []
        for child in node["children"]:
            kept = prune(child)
            if kept is not None:
                children.append(kept)
        is_sel = _is_selectable_for_type(
            path, clean_type, leaves, allowed, exact,
            excluded_prefixes=excluded, types_set=valid_types,
            unit_kind=uk)
        if not is_sel and not children:
            return None
        return {**node, "selectable": is_sel, "children": children}

    def count_tree(items):
        total = 0
        selectable = 0
        for item in items:
            total += 1
            if item.get("selectable"):
                selectable += 1
            sub_t, sub_s = count_tree(item.get("children") or [])
            total += sub_t
            selectable += sub_s
        return total, selectable

    pruned_roots = [kept for root in roots if (kept := prune(root)) is not None]
    total_nodes, selectable_count = count_tree(pruned_roots)
    return {"version": version, "complete": False, "scoped": True,
            "type": clean_type, "unit_kind": clean_kind,
            "answer_point_only": answer_point_only_type(clean_type, uk),
            "allowed_prefixes": [*allowed, *sorted(exact)],
            "total": total_nodes, "selectable_total": selectable_count,
            "leaf_total": selectable_count, "items": pruned_roots}


def knowledge_tree(main_type: str | None = None, unit_kind: str | None = None):
    """目录树；传 main_type（及可选 unit_kind）时只保留允许的知识点分支。"""
    clean_type = main_type.strip() if isinstance(main_type, str) else ""
    clean_kind = _clean_unit_kind(unit_kind) or ""
    return _knowledge_tree_cached(
        KNOWLEDGE_PATH.stat().st_mtime,
        FRAMEWORK_PATH.stat().st_mtime,
        TYPE_KNOWLEDGE_PATH.stat().st_mtime,
        clean_type,
        clean_kind,
    )


def knowledge_catalog_for_prompt():
    """Flat catalog for blind prompts: each node states whether it is a leaf.

    Models otherwise keep drilling past a real leaf (e.g. 应用文/新媒体语篇/
    电子邮件 are three sibling leaves) and invent deeper paths. An explicit
    ``leaf``/``leafTerminal`` marker tells the model "stop here" up front.
    Only presentation changes; the catalog content and paths are unchanged.
    """
    by_path, leaves = knowledge_index()
    points = []
    for path, point in by_path.items():
        is_leaf = path in leaves
        node = {"id": point["id"], "name": point["name"], "path": path,
                "leaf": is_leaf,
                "leafTerminal": "此路径是叶子，到此为止，不得再拼接子级" if is_leaf
                                else "非叶子，需继续选到叶子"}
        points.append(node)
    return {"version": knowledge_version(), "complete": True,
            "leaf_total": len(leaves), "points": points}


def require_knowledge(paths, main_type=None, unit_kind=None):
    """工作台写入只接受目录中的完整叶子路径，不接受自由值或容器强制转换。"""
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError("knowledge 必须是目录叶子路径的字符串列表")
    resolved = resolve_knowledge(paths, main_type, unit_kind=unit_kind)
    if exact_knowledge_paths(main_type, unit_kind=unit_kind) and main_type and any(
            not knowledge_path_allowed(path.strip(), main_type, unit_kind=unit_kind) for path in paths):
        raise ValueError("知识点超出该题型允许的精确目录路径")
    if resolved["unknown"] or resolved["non_leaf"]:
        raise ValueError("知识点必须从目录叶子选择；目录外路径："
                         f"{resolved['unknown']}；中间分类：{resolved['non_leaf']}")
    return list(dict.fromkeys(point["path"] for point in resolved["resolved"]))


def knowledge_search(query: str = "", limit: int = 40, main_type: str | None = None,
                     unit_kind: str | None = None):
    """只返回可选知识点：按题型（及可选 unit_kind）限定范围，目录非叶节点不入库。"""
    by_path, leaves = knowledge_index()
    clean_type = main_type.strip() if isinstance(main_type, str) else ""
    uk = _clean_unit_kind(unit_kind)
    scoped = bool(clean_type and clean_type in types())
    if scoped:
        allowed = _allowed_prefixes(clean_type, unit_kind=uk)
        exact = exact_knowledge_paths(clean_type, unit_kind=uk)
        excluded = _excluded_prefixes(clean_type, unit_kind=uk)
        valid_types = _types_set()
        candidates = sorted(
            path for path in by_path
            if _is_selectable_for_type(
                path, clean_type, leaves, allowed, exact,
                excluded_prefixes=excluded, types_set=valid_types,
                unit_kind=uk))
    else:
        candidates = sorted(leaves)
    keyword = (query or "").strip().lower()
    result = []
    for path in candidates:
        if keyword and keyword not in path.lower():
            continue
        result.append(dict(by_path[path]))
        if len(result) >= max(1, min(limit, 200)):
            break
    return result


def _closest_leaf(path, limit=1):
    """目录里与给定路径最接近的叶子，仅用于把笔误报得可定位（不自动纠正）。"""
    try:
        _by_path, leaves = knowledge_index()
    except Exception:  # noqa: BLE001 - 目录不可用时不阻断报错
        return []
    import difflib
    return difflib.get_close_matches((path or "").strip().strip("/"), list(leaves), n=limit, cutoff=0.78)


@lru_cache(maxsize=4)
def _root_leaf_index_cached(mtime: float) -> dict[tuple[str, str], tuple[str, ...]]:
    """Index catalog leaves by (root_dimension, leaf_name) for unique-leaf lookup."""
    _by_path, leaves = knowledge_index()
    buckets: dict[tuple[str, str], list[str]] = {}
    for leaf_path in sorted(leaves):
        parts = leaf_path.split("/")
        if len(parts) < 2:
            continue
        key = (parts[0], parts[-1])
        buckets.setdefault(key, []).append(leaf_path)
    return {key: tuple(values) for key, values in buckets.items()}


def canonicalize_unique_leaf(path: str, main_type: str | None = None, unit_kind: str | None = None) -> str:
    """Resolve an unambiguous intermediate-segment typo to its unique catalog leaf.

    Only rewrites when:
    1. ``path`` is not already an exact catalog node;
    2. ``path`` has at least ``root/.../leaf`` (2+ segments);
    3. Under the same root dimension, there is **exactly one** catalog leaf with
       the exact same final leaf name (e.g. extra/typo middle folder such as
       ``语法/动词/动词的非谓语形式/动词-ing形式/现在分词`` or
       ``主题/人与社会/社会服务与人沟通/人际交往``);
    4. When ``main_type`` is given, the canonical leaf is allowed for that type.

    Ambiguous leaf names, invented leaf names, or forbidden dimensions are
    returned unchanged so strict validation still rejects them.
    """
    if not isinstance(path, str):
        return path
    cleaned = path.strip().strip("/")
    if not cleaned or "/" not in cleaned:
        return path
    by_path, _leaves = knowledge_index()
    if cleaned in by_path:
        return path
    parts = [part.strip() for part in cleaned.split("/") if part.strip()]
    if len(parts) < 2:
        return path
    matches = _root_leaf_index_cached(KNOWLEDGE_PATH.stat().st_mtime).get((parts[0], parts[-1]), ())
    if len(matches) != 1:
        return path
    canonical = matches[0]
    if main_type and main_type in types():
        if not knowledge_path_allowed(canonical, main_type, unit_kind=unit_kind):
            return path
    return canonical


def canonicalize_knowledge_paths(paths, main_type: str | None = None, unit_kind: str | None = None):
    """Apply :func:`canonicalize_unique_leaf` to a list of paths, deduplicating in order."""
    if not isinstance(paths, list):
        return paths, []
    result = []
    rewrites = []
    seen = set()
    for item in paths:
        if not isinstance(item, str):
            result.append(item)
            continue
        canonical = canonicalize_unique_leaf(item, main_type=main_type, unit_kind=unit_kind)
        if canonical != item:
            rewrites.append((item, canonical))
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result, rewrites


def resolve_knowledge(paths, main_type=None, unit_kind=None):
    """解析完整目录路径；保留名称中的斜线，用目录路径前缀判定叶子。"""
    by_path, leaves = knowledge_index()
    exact = exact_knowledge_paths(main_type, unit_kind=unit_kind) if main_type else set()
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
        elif canonical_path not in leaves and (raw.strip() != path or path not in exact):
            non_leaf.append(path)
        else:
            resolved.append(dict(point))
    return {"resolved": resolved, "unknown": unknown, "non_leaf": non_leaf}


@lru_cache(maxsize=4)
def _types_set_cached(mtime: float) -> frozenset[str]:
    return frozenset(t.get("name", "") for t in _framework_cached(mtime).get("question_types", []) if t.get("name"))


def _types_set() -> frozenset[str]:
    return _types_set_cached(FRAMEWORK_PATH.stat().st_mtime)


@lru_cache(maxsize=4)
def _type_rules_cached(mtime: float):
    return _read(TYPE_KNOWLEDGE_PATH).get("by_type", {})


@lru_cache(maxsize=256)
def _type_dims_cached(mtime: float, main_type: str | None, unit_kind: str | None = None) -> tuple[dict, ...]:
    if not isinstance(main_type, str) or not main_type:
        return ()
    rule = _type_rules_cached(mtime).get(main_type)
    if not isinstance(rule, dict):
        return ()
    uk = _clean_unit_kind(unit_kind)
    by_kind = rule.get("by_unit_kind")
    if uk and isinstance(by_kind, dict) and isinstance(by_kind.get(uk), dict):
        dims = by_kind[uk].get("dims")
        if isinstance(dims, list):
            return tuple(dims)
    dims = rule.get("dims")
    return tuple(dims) if isinstance(dims, list) else ()


def type_dims(main_type: str | None, unit_kind: str | None = None) -> list[dict]:
    """返回指定题型（及可选 group/standalone 形态）的维度定义列表。"""
    return list(_type_dims_cached(TYPE_KNOWLEDGE_PATH.stat().st_mtime, main_type, _clean_unit_kind(unit_kind)))


def has_answer_point_dim(main_type: str | None, unit_kind: str | None = None) -> bool:
    """该题型（在指定形态下）是否启用「答案考点」自动词条派生。"""
    return any(dim.get("dim") == "答案考点" for dim in type_dims(main_type, unit_kind))


def answer_point_only_type(main_type: str | None, unit_kind: str | None = None) -> bool:
    """该题型在指定形态下是否只保留独立答案考点（目录 knowledge 应为空）。"""
    dims = type_dims(main_type, unit_kind)
    return bool(dims) and all(dim.get("dim") == "答案考点" and dim.get("separate") for dim in dims)


@lru_cache(maxsize=256)
def _exact_paths_cached(mtime: float, main_type: str | None, unit_kind: str | None = None) -> frozenset[str]:
    rules = _type_rules_cached(mtime)
    if main_type is None:
        all_dims = [dim for spec in rules.values() for dim in spec.get("dims", [])]
        for spec in rules.values():
            for sub in (spec.get("by_unit_kind") or {}).values():
                if isinstance(sub, dict):
                    all_dims.extend(sub.get("dims") or [])
    else:
        all_dims = _type_dims_cached(mtime, main_type, _clean_unit_kind(unit_kind))
    return frozenset(dim["branch"] + "/" + dim["scope"]
                     for dim in all_dims if dim.get("show") == "exact")


@lru_cache(maxsize=256)
def _excluded_prefixes_cached(mtime: float, main_type: str | None, unit_kind: str | None = None) -> tuple[str, ...]:
    excluded = []
    for dim in _type_dims_cached(mtime, main_type, _clean_unit_kind(unit_kind)):
        for item in dim.get("exclude", []) or []:
            if isinstance(item, str) and item.strip() and item.strip() not in excluded:
                excluded.append(item.strip())
    return tuple(excluded)


@lru_cache(maxsize=256)
def _allowed_prefixes_cached(mtime: float, main_type: str | None, unit_kind: str | None = None) -> tuple[str, ...]:
    prefixes = []
    for dim in _type_dims_cached(mtime, main_type, _clean_unit_kind(unit_kind)):
        if dim.get("show") == "exact" or dim.get("separate"):
            continue
        # branch 的复合值是多个维度根，不是知识点路径。
        for branch in dim.get("branch", "").split("/"):
            prefix = branch + ("/" + dim["scope"] if dim.get("scope") else "")
            # 其他的旧规则仍写「阅读技能」；现有目录已迁至阅读设题角度。
            if prefix == "阅读技能":
                prefix = "设题角度/阅读"
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)
    return tuple(prefixes)


def type_knowledge_rules():
    """27 类的维度来源；只使用 by_type.dims（及可选 by_unit_kind），不使用旧 defaults/hints。"""
    return _type_rules_cached(TYPE_KNOWLEDGE_PATH.stat().st_mtime)


def exact_knowledge_paths(main_type=None, unit_kind=None):
    """Catalog nodes selectable only as exact paths for the given question type."""
    if main_type is not None and not isinstance(main_type, str):
        return set()
    return set(_exact_paths_cached(TYPE_KNOWLEDGE_PATH.stat().st_mtime, main_type, _clean_unit_kind(unit_kind)))


def knowledge_path_allowed(path, main_type, prefixes=None, *, excluded_prefixes=None, exact_paths=None, unit_kind=None):
    if prefixes is None:
        prefixes = _allowed_prefixes(main_type, unit_kind=unit_kind)
    # 某些题型在允许维度内部仍排除特定子分支（如单词拼写不用「词汇/词汇辨析」）。
    if excluded_prefixes is None:
        excluded_prefixes = _excluded_prefixes(main_type, unit_kind=unit_kind)
    if any(_under(path, excluded) for excluded in excluded_prefixes):
        return False
    if exact_paths is None:
        exact_paths = exact_knowledge_paths(main_type, unit_kind=unit_kind)
    if exact_paths:
        return path in exact_paths
    return any(_under(path, prefix) for prefix in prefixes)


def _excluded_prefixes(main_type, unit_kind=None):
    """题型级排除的知识点子前缀（dim.exclude）。

    例如单词拼写虽允许「词汇」维度，但不用「词汇/词汇辨析」这一子类：
    拼写/词形落 词汇/拼写与词形 而不是 词汇辨析/<词性>辨析。
    """
    return list(_excluded_prefixes_cached(TYPE_KNOWLEDGE_PATH.stat().st_mtime, main_type, _clean_unit_kind(unit_kind)))


def _allowed_prefixes(main_type, unit_kind=None):
    return list(_allowed_prefixes_cached(TYPE_KNOWLEDGE_PATH.stat().st_mtime, main_type, _clean_unit_kind(unit_kind)))


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


def _check_labels(values, prefixes=None, unit_kind=None):
    if not isinstance(values, dict):
        return ["标签必须是对象"]
    problems = []
    uk = _clean_unit_kind(unit_kind if unit_kind is not None else values.get("unit_kind"))
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
    resolved = resolve_knowledge(paths, main_type, unit_kind=uk)
    if resolved["unknown"]:
        # 路径笔误（尤其主题里的「沟通/际沟通」「与/于」之类）很常见：报出最接近的
        # 目录叶子，方便模型/运维一次改对；只提示、不自动替换、不放宽校验。
        hinted = []
        for bad in resolved["unknown"]:
            guess = _closest_leaf(bad)
            hinted.append(f"{bad}（相近叶子：{guess[0]}）" if guess else str(bad))
        problems.append(f"知识点路径不在目录里：{hinted}")
    if resolved["non_leaf"]:
        problems.append(f"知识点必须是叶子，这些是中间层：{resolved['non_leaf']}")
    if main_type in types():
        allowed = _allowed_prefixes(main_type, unit_kind=uk) if prefixes is None else prefixes
        bad = [path for path in paths
               if not knowledge_path_allowed(path, main_type, allowed, unit_kind=uk)
               or (not has_answer_point_dim(main_type, uk)
                   and (_under(path, "词汇/考察词汇") or _under(path, "词汇/答案考点")))]
        if bad:
            allowed_desc = "、".join([*allowed, *sorted(exact_knowledge_paths(main_type, unit_kind=uk))]) or "仅答案考点（目录知识点留空）"
            problems.append(f"「{main_type}」知识点超出允许维度/范围（{allowed_desc}）：{bad}")
    return problems


def check_labels(values: dict, unit_kind: str | None = None) -> list[str]:
    """草稿校验：可缺主类、子类、知识点；已填值须合法且符合题型维度。

    列表字段必须是字符串列表；只有单词拼写允许多个子类。不检查完整性、
    组级必带维度或子题上下文；正式批次请用 check_question_labels。
    """
    return _check_labels(values, unit_kind=unit_kind)


def _unit_dims(unit_kind: str | None = None):
    uk = _clean_unit_kind(unit_kind)
    result = {}
    for main_type in types():
        allow = _allowed_prefixes(main_type, unit_kind=uk)
        # 其他的体裁/主题由实际材料决定，不能凭题型强制推定。
        need = [dim for dim in ("语篇体裁", "主题") if dim in allow] if main_type != "其他" else []
        result[main_type] = {
            "allow": allow,
            "forbid": ["词汇", "语法", "设题角度"] if main_type in ("完形填空", "语法选择") else [],
            "need": need,
        }
    return result


# 保留公开常量供旧调用方读取；校验每次读取按 mtime 缓存的最新目录。
UNIT_DIMS = _unit_dims("group")


def check_unit_dims(main_type: str, knowledge, unit_kind: str | None = "group") -> list[str]:
    """组级维度校验（含听力/阅读范围）；不代替目录叶子或完整题目校验。"""
    problems = []
    uk = _clean_unit_kind(unit_kind)
    main_type = _main_type(main_type, problems)
    if not main_type:
        return problems + ["主类不能为空"]
    paths = _string_list(knowledge, "knowledge", problems)
    rule = _unit_dims(uk).get(main_type)
    if not rule:
        return problems
    banned = [path for path in paths if any(_under(path, dim) for dim in rule["forbid"])]
    if banned:
        # 这段文案由现有工作台/测试依赖。
        problems.append(f"「{main_type}」组级不挂 {'/'.join(rule['forbid'])}：{banned}；"
                        f"整组题型不再把这些维度下放到子题")
    outside = [path for path in paths if path not in banned and
               not knowledge_path_allowed(path, main_type, rule["allow"], unit_kind=uk)]
    if outside:
        allowed_desc = "、".join([*rule["allow"], *sorted(exact_knowledge_paths(main_type, unit_kind=uk))]) or "仅答案考点（目录知识点留空）"
        problems.append(f"「{main_type}」组级知识点超出允许维度/范围（{allowed_desc}）：{outside}")
    missing = [dim for dim in rule["need"] if not any(_under(path, dim) for path in paths)]
    if missing:
        problems.append(f"「{main_type}」组级缺必带维度：{'/'.join(missing)}")
    return problems


def check_per_question_required(main_type: str, knowledge) -> list[str]:
    """逐题（per-question）题型的必带维度校验。

    与 check_unit_dims 同口径读取 _unit_dims("standalone")[main_type]["need"]，但作用于单个
    题目/子题自身的标签，而非整组。整组题型由 check_unit_dims 在组级负责，这里
    只对非整组题型生效；单词拼写/单项填空/翻译/语音题等 need 为空者不受影响。
    """
    if whole_unit_type(main_type):
        return []
    # 书面表达不是客观题（规则 23）：知识点标签可空/可只有部分维度，缺少体裁或主题
    # 不算硬错误，因此不做逐题必带维度强制（避免合法作文被判缺失而卡流程）。
    if main_type == "书面表达":
        return []
    rule = _unit_dims("standalone").get(main_type)
    if not rule or not rule["need"]:
        return []
    problems = []
    paths = _string_list(knowledge, "knowledge", problems)
    if problems:
        return problems
    missing = [dim for dim in rule["need"] if not any(_under(path, dim) for path in paths)]
    if missing:
        problems.append(f"「{main_type}」缺必带维度：{'/'.join(missing)}")
    return problems


_READING_TYPES = {"阅读理解", "任务型阅读", "读写综合"}
_CLOZE_TYPES = {"完形填空", "语法选择"}


# 整篇/整个大题只做组级标签的题型；子题不要求、也不承载细分知识点（knowledge=[]）。
# 注：阅读理解设题角度仍标在组题级 knowledge，但具体设题角度须逐小题分析每道小题的问法后去重汇总。
WHOLE_UNIT_TYPES = frozenset((
    "阅读理解", "任务型阅读", "完形填空", "语法选择", "听力信息转换",
    "读写综合", "选词填空", "短文填空", "教材语篇填空",
    "听力短文理解", "听力长对话理解", "补全对话",
))


def whole_unit_type(main_type) -> bool:
    return str(main_type or "") in WHOLE_UNIT_TYPES


def check_question_labels(question) -> list[str]:
    """正式批次单元的完整校验，返回问题列表而不修改输入。

    支持 question_type/type、filterMetadata.subtypes/categories、可选的
    knowledgeIds/knowledge_ids。子题未声明题型时只继承题型用于规则选择，
    不继承知识点或子类；整组题型子题 knowledge 可为空；
    选词填空整题（group）仅保留独立答案考点，允许目录 knowledge=[]；
    独立单题按自身题型（standalone 维度）校验。
    """
    def validate(item, parent_type=None, location="题目", parent_is_group=False):
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
        children = item.get("subquestions", [])
        if not isinstance(children, list):
            problems.append("subquestions 必须是列表")
            children = []
        is_group = bool(children) or item.get("unit_kind") == "group"
        effective_kind = "group" if (is_group and parent_type is None) else "standalone"
        values = {"type": main_type, "unit_kind": effective_kind}
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
        ap_only = parent_type is None and answer_point_only_type(main_type, effective_kind)
        # 整组题型只要求组级 knowledge 非空，子题可空；
        # 仅答案考点题型（如选词填空整题）目录 knowledge 允许为空（答案词条另行派生）。
        child_of_whole = parent_type is not None and (whole_unit_type(parent_type) or parent_is_group)
        if not isinstance(paths, list) or (not paths and not child_of_whole and not ap_only):
            problems.append("knowledge 必须是非空知识点列表")
        prefixes = None
        if parent_type in _CLOZE_TYPES:
            prefixes = ["词汇", "语法"]
        elif parent_type in _READING_TYPES:
            prefixes = [p for p in _allowed_prefixes(main_type) if p != "语篇体裁"]
        problems.extend(_check_labels(values, prefixes, unit_kind=effective_kind))
        clean_paths = [p.strip() for p in paths if isinstance(p, str) and p.strip()] if isinstance(paths, list) else []
        if (parent_type in _READING_TYPES and not whole_unit_type(parent_type)
                and not any(_under(p, "设题角度/阅读") for p in clean_paths)):
            problems.append("阅读子题必须包含设题角度/阅读叶子")
        expected_ids = [point["id"] for point in resolve_knowledge(clean_paths, main_type, unit_kind=effective_kind)["resolved"]]
        for key in ("knowledgeIds", "knowledge_ids"):
            if key in item:
                ids = _string_list(item[key], key, problems)
                # IDs 是无序标签集合；同一 ID 不应重复，不能漏项或附加项。
                if len(ids) != len(set(ids)) or set(ids) != set(expected_ids):
                    problems.append(f"{key} 与 knowledge 目录叶子 ID 不一致")
        if is_group and parent_type is None and main_type in types():
            problems.extend(check_unit_dims(main_type, clean_paths, unit_kind="group"))
        elif parent_type is None and main_type in types():
            problems.extend(check_per_question_required(main_type, clean_paths))
        for index, child in enumerate(children, 1):
            problems.extend(validate(child, main_type, f"子题[{index}]", parent_is_group=is_group))
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
