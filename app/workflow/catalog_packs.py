# -*- coding: utf-8 -*-
"""按维度拆分的知识点目录包（catalog-packs）与同维度分桶策略。

单一真相源（Single Source of Truth）：
  `app/references/knowledge-catalog.json` + `app/references/type-knowledge-map.json`。
`app/references/catalog-packs/` 下生成的分包文件与 `manifest.json` 仅为确定性派生视图，
运行时严格校验 `source_sha256`；若缺失、损坏或与主目录摘要不一致，自动回退到内存实时派生，
绝不静默使用过期目录包。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = ROOT / "references" / "knowledge-catalog.json"
DEFAULT_PACKS_DIR = ROOT / "references" / "catalog-packs"
SCHEMA_VERSION = "catalog-packs/v1"

# 10 个确定性维度包：8 个互不相交的基础叶子包覆盖全库 100% 叶子，
# 2 个专用约束包从物理层面拦截高频规则漂移：
# - vocab-spelling：排除「词汇/词汇辨析」，供「单词拼写」专用；
# - phonetics-keyword：仅含精确节点「语音/基本读音」，供「听力关键词语选择」专用。
PACK_SPECS = (
    {
        "name": "theme",
        "base": True,
        "prefixes": ("主题",),
        "excludes": (),
        "exact": (),
        "description": "主题维度全部叶子（人与自我/人与社会/人与自然）",
    },
    {
        "name": "genre",
        "base": True,
        "prefixes": ("语篇体裁",),
        "excludes": (),
        "exact": (),
        "description": "语篇体裁维度全部叶子",
    },
    {
        "name": "angle-reading",
        "base": True,
        "prefixes": ("设题角度/阅读",),
        "excludes": (),
        "exact": (),
        "description": "设题角度/阅读分支全部叶子",
    },
    {
        "name": "angle-listening",
        "base": True,
        "prefixes": ("设题角度/听力",),
        "excludes": (),
        "exact": (),
        "description": "设题角度/听力分支全部叶子",
    },
    {
        "name": "vocab-full",
        "base": True,
        "prefixes": ("词汇",),
        "excludes": (),
        "exact": (),
        "description": "词汇维度全部叶子（含拼写与词形/构词法/短语/词汇辨析）",
    },
    {
        "name": "vocab-spelling",
        "base": False,
        "prefixes": ("词汇",),
        "excludes": ("词汇/词汇辨析",),
        "exact": (),
        "description": "单词拼写专用词汇包：物理排除「词汇/词汇辨析」分支",
    },
    {
        "name": "grammar",
        "base": True,
        "prefixes": ("语法",),
        "excludes": (),
        "exact": (),
        "description": "语法维度全部叶子",
    },
    {
        "name": "phonetics",
        "base": True,
        "prefixes": ("语音",),
        "excludes": (),
        "exact": (),
        "description": "语音维度全部叶子（供语音题/单项填空/单词拼写等）",
    },
    {
        "name": "phonetics-keyword",
        "base": False,
        "prefixes": (),
        "excludes": (),
        "exact": ("语音/基本读音",),
        "description": "听力关键词语选择专用包：仅含精确节点「语音/基本读音」，禁止下钻",
    },
    {
        "name": "pragmatics",
        "base": True,
        "prefixes": ("语用",),
        "excludes": (),
        "exact": (),
        "description": "语用维度全部叶子（情景交际/谚语习语）",
    },
)

PACK_ORDER = tuple(spec["name"] for spec in PACK_SPECS)
BASE_PACKS = tuple(spec["name"] for spec in PACK_SPECS if spec["base"])
_SPEC_BY_NAME = {spec["name"]: spec for spec in PACK_SPECS}

_PREFIX_TO_PACK = {
    "主题": "theme",
    "语篇体裁": "genre",
    "设题角度/阅读": "angle-reading",
    "设题角度/听力": "angle-listening",
    "词汇": "vocab-full",
    "语法": "grammar",
    "语音": "phonetics",
    "语用": "pragmatics",
}

# 标准维度包组合 → 可读分桶名（保证同一分桶内的题型需要完全一致的维度包，
# 绝不把跨维度题型混进同一分片导致目录膨胀或串维度误标）。
_SIGNATURE_TO_BUCKET = {
    ("phonetics-keyword",): "phonetics_keyword",
    ("phonetics",): "phonetics",
    ("theme", "angle-listening"): "listening_dialogue",
    ("theme", "genre", "angle-listening"): "listening_passage",
    ("theme", "genre", "angle-listening", "vocab-full", "grammar"): "listening_mixed",
    ("theme", "genre"): "discourse",
    ("theme", "genre", "angle-reading"): "reading",
    ("theme", "genre", "pragmatics"): "dialogue",
    ("theme", "genre", "vocab-full", "grammar"): "mixed_cloze",
    ("vocab-spelling", "grammar", "phonetics"): "language_spelling",
    ("vocab-full", "grammar"): "language_core",
    ("vocab-full", "grammar", "pragmatics"): "language_pragmatic",
    ("vocab-full", "grammar", "phonetics", "pragmatics"): "language_full",
    ("theme", "genre", "angle-reading", "vocab-full", "grammar", "phonetics", "pragmatics"): "other",
}

_LOAD_CACHE: dict[tuple, dict] = {}


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_paths(paths: list[str]) -> str:
    encoded = json.dumps(paths, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def _extract_catalog_paths(catalog: dict) -> tuple[list[str], set[str], set[str]]:
    """Return (all_paths_in_order, leaf_set, all_path_set) from a catalog dict."""
    points = catalog.get("points") if isinstance(catalog, dict) else None
    if not isinstance(points, list):
        raise ValueError("knowledge-catalog.json must contain a 'points' list")
    all_paths = [
        p["path"].strip()
        for p in points
        if isinstance(p, dict) and isinstance(p.get("path"), str) and p["path"].strip()
    ]
    parent_prefixes = set()
    for p in all_paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            parent_prefixes.add("/".join(parts[:i]))
    leaves = {p for p in all_paths if p not in parent_prefixes}
    return all_paths, leaves, set(all_paths)


def build_packs(catalog: dict | None = None, *, catalog_path: Path | None = None,
                source_sha256: str | None = None) -> dict:
    """Deterministically derive all dimension packs from knowledge-catalog.json."""
    path = Path(catalog_path or DEFAULT_CATALOG_PATH)
    if catalog is None:
        raw = path.read_bytes()
        source_sha256 = source_sha256 or _sha256_bytes(raw)
        catalog = json.loads(raw.decode("utf-8-sig"))
    elif source_sha256 is None:
        source_sha256 = _sha256_bytes(
            json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    all_paths, leaves, all_set = _extract_catalog_paths(catalog)
    leaf_order = [p for p in all_paths if p in leaves]
    packs: dict[str, dict] = {}
    covered_by_base: set[str] = set()
    overlap_in_base: list[str] = []

    for spec in PACK_SPECS:
        name = spec["name"]
        if spec["exact"]:
            selected = [p for p in spec["exact"] if p in all_set]
        else:
            prefixes = spec["prefixes"]
            excludes = spec["excludes"]
            selected = [
                p for p in leaf_order
                if any(_under(p, pref) for pref in prefixes)
                and not any(_under(p, ex) for ex in excludes)
            ]
        if spec["base"]:
            for p in selected:
                if p in covered_by_base:
                    overlap_in_base.append(p)
                covered_by_base.add(p)
        packs[name] = {
            "name": name,
            "base": bool(spec["base"]),
            "description": spec["description"],
            "count": len(selected),
            "sha256": _sha256_paths(selected),
            "paths": selected,
        }

    uncovered = [p for p in leaf_order if p not in covered_by_base]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_file": "references/knowledge-catalog.json",
        "source_sha256": source_sha256,
        "catalog_version": str(catalog.get("version") or ""),
        "catalog_version_id": str(catalog.get("versionId") or ""),
        "total_nodes": len(all_paths),
        "total_leaves": len(leaf_order),
        "base_packs": list(BASE_PACKS),
        "packs": {
            name: {
                "file": f"{name}.json",
                "base": info["base"],
                "count": info["count"],
                "sha256": info["sha256"],
                "description": info["description"],
            }
            for name, info in packs.items()
        },
    }
    return {
        "manifest": manifest,
        "packs": packs,
        "all_paths": all_paths,
        "leaf_order": leaf_order,
        "uncovered_leaves": uncovered,
        "overlap_in_base": overlap_in_base,
        "catalog_meta": {
            k: v for k, v in catalog.items()
            if k not in ("points", "paths", "packs", "exact_paths_by_type", "note")
        },
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_packs(packs_dir: Path | None = None, catalog_path: Path | None = None) -> dict:
    """Materialize read-only catalog-packs/ files and verify integrity."""
    out_dir = Path(packs_dir or DEFAULT_PACKS_DIR)
    cat_path = Path(catalog_path or DEFAULT_CATALOG_PATH)
    built = build_packs(catalog_path=cat_path)
    if built["uncovered_leaves"]:
        raise ValueError(f"Catalog has uncovered leaves: {built['uncovered_leaves'][:5]}")
    if built["overlap_in_base"]:
        raise ValueError(f"Base packs have overlapping leaves: {built['overlap_in_base'][:5]}")

    out_dir.mkdir(parents=True, exist_ok=True)
    expected_files = {"manifest.json"}
    for name, info in built["packs"].items():
        fname = f"{name}.json"
        expected_files.add(fname)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "pack": name,
            "base": info["base"],
            "description": info["description"],
            "source_sha256": built["manifest"]["source_sha256"],
            "count": info["count"],
            "sha256": info["sha256"],
            "paths": info["paths"],
        }
        _atomic_write_text(out_dir / fname, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    _atomic_write_text(
        out_dir / "manifest.json",
        json.dumps(built["manifest"], ensure_ascii=False, indent=2) + "\n",
    )
    for stray in out_dir.glob("*.json"):
        if stray.name not in expected_files:
            stray.unlink(missing_ok=True)

    _LOAD_CACHE.clear()
    report = verify_packs(out_dir, cat_path)
    if not report["ok"]:
        raise ValueError("Generated catalog-packs failed verification: " + "; ".join(report["errors"]))
    return report


def verify_packs(packs_dir: Path | None = None, catalog_path: Path | None = None) -> dict:
    """Verify materialized catalog-packs/ against current knowledge-catalog.json."""
    out_dir = Path(packs_dir or DEFAULT_PACKS_DIR)
    cat_path = Path(catalog_path or DEFAULT_CATALOG_PATH)
    built = build_packs(catalog_path=cat_path)
    errors: list[str] = []

    if built["uncovered_leaves"]:
        errors.append(f"uncovered_leaves:{len(built['uncovered_leaves'])}")
    if built["overlap_in_base"]:
        errors.append(f"overlap_in_base:{len(built['overlap_in_base'])}")

    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        errors.append(f"missing:{manifest_path.name}")
        return {"ok": False, "source_sha256": built["manifest"]["source_sha256"], "errors": errors, "packs": {}}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"manifest_invalid:{exc}")
        return {"ok": False, "source_sha256": built["manifest"]["source_sha256"], "errors": errors, "packs": {}}

    if manifest.get("source_sha256") != built["manifest"]["source_sha256"]:
        errors.append("source_sha256_mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")

    pack_summary = {}
    for name, expected in built["packs"].items():
        pfile = out_dir / f"{name}.json"
        if not pfile.is_file():
            errors.append(f"missing_pack:{name}")
            continue
        try:
            doc = json.loads(pfile.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"pack_invalid:{name}:{exc}")
            continue
        if doc.get("source_sha256") != built["manifest"]["source_sha256"]:
            errors.append(f"pack_source_sha_mismatch:{name}")
        if doc.get("paths") != expected["paths"]:
            errors.append(f"pack_paths_mismatch:{name}")
        if doc.get("sha256") != expected["sha256"]:
            errors.append(f"pack_sha_mismatch:{name}")
        pack_summary[name] = expected["count"]

    return {
        "ok": not errors,
        "source_sha256": built["manifest"]["source_sha256"],
        "total_leaves": built["manifest"]["total_leaves"],
        "errors": errors,
        "packs": pack_summary,
    }


def load_packs(*, packs_dir: Path | None = None, catalog_path: Path | None = None) -> dict:
    """Load verified packs (materialized if fresh, else live in-memory fallback)."""
    out_dir = Path(packs_dir or DEFAULT_PACKS_DIR)
    cat_path = Path(catalog_path or DEFAULT_CATALOG_PATH)
    cat_stat = cat_path.stat()
    manifest_path = out_dir / "manifest.json"
    try:
        m_stat = manifest_path.stat()
        m_key = (m_stat.st_mtime_ns, m_stat.st_size)
    except OSError:
        m_key = None
    cache_key = (str(out_dir), str(cat_path), cat_stat.st_mtime_ns, cat_stat.st_size, m_key)
    hit = _LOAD_CACHE.get(cache_key)
    if hit is not None:
        return hit

    raw_cat = cat_path.read_bytes()
    source_sha = _sha256_bytes(raw_cat)
    loaded = None
    if m_key is not None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            if (isinstance(manifest, dict)
                    and manifest.get("schema_version") == SCHEMA_VERSION
                    and manifest.get("source_sha256") == source_sha
                    and isinstance(manifest.get("packs"), dict)):
                packs_map = {}
                ok = True
                for name in PACK_ORDER:
                    entry = manifest["packs"].get(name)
                    if not isinstance(entry, dict):
                        ok = False
                        break
                    pfile = out_dir / str(entry.get("file") or f"{name}.json")
                    doc = json.loads(pfile.read_text(encoding="utf-8-sig"))
                    paths = doc.get("paths")
                    if (not isinstance(paths, list)
                            or doc.get("source_sha256") != source_sha
                            or doc.get("sha256") != entry.get("sha256")
                            or _sha256_paths(paths) != entry.get("sha256")):
                        ok = False
                        break
                    packs_map[name] = {
                        "name": name,
                        "base": bool(_SPEC_BY_NAME[name]["base"]),
                        "description": _SPEC_BY_NAME[name]["description"],
                        "count": len(paths),
                        "sha256": entry["sha256"],
                        "paths": paths,
                    }
                if ok:
                    cat_doc = json.loads(raw_cat.decode("utf-8-sig"))
                    all_paths, leaves, _ = _extract_catalog_paths(cat_doc)
                    loaded = {
                        "source": "materialized",
                        "manifest": manifest,
                        "packs": packs_map,
                        "all_paths": all_paths,
                        "leaf_order": [p for p in all_paths if p in leaves],
                        "catalog_meta": {
                            k: v for k, v in cat_doc.items()
                            if k not in ("points", "paths", "packs", "exact_paths_by_type", "note")
                        },
                    }
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            loaded = None

    if loaded is None:
        cat_doc = json.loads(raw_cat.decode("utf-8-sig"))
        loaded = build_packs(cat_doc, catalog_path=cat_path, source_sha256=source_sha)
        loaded["source"] = "live_fallback"

    if len(_LOAD_CACHE) > 16:
        _LOAD_CACHE.clear()
    _LOAD_CACHE[cache_key] = loaded
    return loaded


def packs_for_type(main_type: str | None) -> list[str] | None:
    """Map one canonical question type to its exact ordered list of catalog packs.

    Returns None for unknown/empty types so callers safely fall back to full catalog.
    """
    if not isinstance(main_type, str) or not main_type.strip():
        return None
    ty = main_type.strip()
    from workbench import taxonomy
    rules = taxonomy.type_knowledge_rules()
    if ty not in rules:
        return None
    exact = taxonomy.exact_knowledge_paths(ty)
    allowed = taxonomy._allowed_prefixes(ty)
    excluded = set(taxonomy._excluded_prefixes(ty))
    if not exact and not allowed:
        return None

    chosen: set[str] = set()
    if exact:
        if exact == {"语音/基本读音"}:
            chosen.add("phonetics-keyword")
        else:
            return None
    else:
        for prefix in allowed:
            if prefix == "词汇" and "词汇/词汇辨析" in excluded:
                chosen.add("vocab-spelling")
            elif prefix in _PREFIX_TO_PACK:
                chosen.add(_PREFIX_TO_PACK[prefix])
            else:
                return None

    return [name for name in PACK_ORDER if name in chosen]


def bucket_for_type(main_type: str | None) -> str:
    """Return the deterministic dimension-bucket ID for a question type."""
    pack_list = packs_for_type(main_type)
    if not pack_list:
        return "unknown"
    sig = tuple(pack_list)
    if sig in _SIGNATURE_TO_BUCKET:
        return _SIGNATURE_TO_BUCKET[sig]
    return "packs:" + "+".join(sig)


def packs_for_types(types: Iterable[str | None] | None, *, catalog: dict | None = None,
                    packs_dir: Path | None = None) -> dict:
    """Assemble the exact catalog pack union + selectable paths for a set of question types.

    Guarantees:
    - If ``types`` is empty or contains any unknown/invalid type, falls back to the
      complete leaf set (+ all exact paths) and sets ``fallback=True``.
    - Non-leaf exact paths (like ``语音/基本读音``) are NEVER leaked to types whose
      rules do not allow exact selection.
    - ``单词拼写`` alone receives ``vocab-spelling`` (physically excluding ``词汇/词汇辨析/*``).
    - Output ``paths`` preserves the authoritative ``knowledge-catalog.json`` ordering.
    """
    from workbench import taxonomy

    data = (
        build_packs(catalog)
        if catalog is not None
        else load_packs(packs_dir=packs_dir)
    )
    rules = taxonomy.type_knowledge_rules()
    type_list = list(types) if types is not None else []
    per_type_packs: list[list[str]] = []
    fallback = not type_list
    if not fallback:
        for ty in type_list:
            plist = packs_for_type(ty)
            if not plist:
                fallback = True
                break
            per_type_packs.append(plist)

    if fallback:
        exact_by_type = {
            ty: sorted(taxonomy.exact_knowledge_paths(ty))
            for ty in rules if taxonomy.exact_knowledge_paths(ty)
        }
        exact_all = {p for ps in exact_by_type.values() for p in ps}
        leaf_set = set(data["leaf_order"])
        kept_paths = [p for p in data["all_paths"] if p in leaf_set or p in exact_all]
        pack_names = list(BASE_PACKS)
        if "语音/基本读音" in exact_all and "phonetics-keyword" in data["packs"]:
            pack_names.append("phonetics-keyword")
        return {
            "fallback": True,
            "packs": pack_names,
            "paths": kept_paths,
            "exact_paths_by_type": exact_by_type,
            "catalog_meta": data["catalog_meta"],
            "source": data.get("source", "live"),
        }

    chosen_set = {name for plist in per_type_packs for name in plist}
    # If both vocab-full and vocab-spelling are needed by a mixed shard, vocab-full subsumes vocab-spelling.
    if "vocab-full" in chosen_set and "vocab-spelling" in chosen_set:
        chosen_set.discard("vocab-spelling")
    ordered_packs = [name for name in PACK_ORDER if name in chosen_set]

    pack_path_set: set[str] = set()
    for name in ordered_packs:
        pack_path_set.update(data["packs"][name]["paths"])

    # Double-check every path against taxonomy._is_selectable_for_type so custom
    # exclusions (such as _DISALLOWED_VOCAB_TYPES) stay 100% identical to taxonomy.
    leaves_set = set(data["leaf_order"])
    clean_types = [ty.strip() for ty in type_list if isinstance(ty, str) and ty.strip()]
    kept_paths = [
        p for p in data["all_paths"]
        if p in pack_path_set
        and any(taxonomy._is_selectable_for_type(p, ty, leaves_set) for ty in clean_types)
    ]
    exact_by_type = {
        ty: sorted(taxonomy.exact_knowledge_paths(ty))
        for ty in clean_types if taxonomy.exact_knowledge_paths(ty)
    }
    return {
        "fallback": False,
        "packs": ordered_packs,
        "paths": kept_paths,
        "exact_paths_by_type": exact_by_type,
        "catalog_meta": data["catalog_meta"],
        "source": data.get("source", "live"),
    }


def contiguous_bucket_runs(units: list[dict], type_resolver: Callable[[dict], str | None]) -> list[tuple[str, list[dict]]]:
    """Group units into contiguous runs sharing the same dimension bucket.

    Preserves exact original unit order (zero reordering) and keeps source spans
    contiguous while preventing cross-dimension pollution inside a shard.
    """
    runs: list[tuple[str, list[dict]]] = []
    for unit in units:
        bucket = bucket_for_type(type_resolver(unit))
        if not runs or runs[-1][0] != bucket:
            runs.append((bucket, [unit]))
        else:
            runs[-1][1].append(unit)
    return runs


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify dimension-split knowledge catalog packs.")
    parser.add_argument("action", nargs="?", default="summary", choices=("build", "check", "summary"))
    parser.add_argument("--check", action="store_true", help="Alias for action=check")
    parser.add_argument("--packs-dir", type=Path, default=DEFAULT_PACKS_DIR)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    args = parser.parse_args(argv)
    action = "check" if args.check else args.action
    if action == "build":
        report = write_packs(args.packs_dir, args.catalog)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if action == "check":
        report = verify_packs(args.packs_dir, args.catalog)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    built = build_packs(catalog_path=args.catalog)
    summary = {
        "source_sha256": built["manifest"]["source_sha256"],
        "total_leaves": built["manifest"]["total_leaves"],
        "packs": {k: v["count"] for k, v in built["packs"].items()},
        "uncovered_leaves": len(built["uncovered_leaves"]),
        "overlap_in_base": len(built["overlap_in_base"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
