# -*- coding: utf-8 -*-
"""自检：确认工作流本身是正常的，而不是只在某一次跑通。

检查四类：
1. 环境：Python 版本、依赖模块；
2. 规则文件：题型/知识点/难度目录能否解析；
3. 契约：阶段 id 唯一、依赖存在、无环、顺序自洽；
4. 闸门：用临时目录跑一组已知用例，确认该拦的拦、该放的放。

最后扫描已有批次清单，报告状态。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from . import checks, contracts, manifest as mf, review

REQUIRED_FILES = (
    "workflow/__init__.py",
    "workflow/runner.py",
    "workflow/contracts.py",
    "workflow/manifest.py",
    "workflow/review.py",
    "workflow/report.py",
    "workflow/ingest.py",
    "workflow/inventory.py",
    "workflow/doctor.py",
    "workflow/preview.py",
    "workflow/templates/preview.html",
    "workflow/auto.py",
    "workflow/pipelines/mudu-question-intake.workflow.js",
)

RULE_FILES = (
    "references/filter-framework.json",
    "references/knowledge-catalog.json",
    "references/difficulty-category.json",
    "references/type-knowledge-map.json",
)


def _check(name: str, ok: bool, detail: str, hint: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail, "hint": hint}


def check_env() -> list[dict]:
    out = [_check("Python 版本", sys.version_info >= (3, 9),
                  f"{sys.version_info.major}.{sys.version_info.minor}")]
    out.append(_check("标准库可用", True, "json / pathlib / hashlib / subprocess"))
    return out


def check_files(skill_root: Path) -> list[dict]:
    out = []
    for rel in REQUIRED_FILES:
        out.append(_check(f"文件 {rel}", (skill_root / rel).is_file(),
                          "存在" if (skill_root / rel).is_file() else "缺失"))
    return out


def check_rules(skill_root: Path) -> list[dict]:
    out = []
    for rel in RULE_FILES:
        path = skill_root / rel
        if not path.is_file():
            out.append(_check(f"规则 {rel}", False, "缺失"))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            size = len(data) if hasattr(data, "__len__") else 0
            out.append(_check(f"规则 {rel}", True, f"可解析，{size} 项"))
        except json.JSONDecodeError as exc:
            out.append(_check(f"规则 {rel}", False, f"JSON 错误：{exc}", "修复后再跑"))
    return out


def check_contracts() -> list[dict]:
    out = []
    ids = [s.id for s in contracts.STAGES]
    out.append(_check("阶段 id 唯一", len(ids) == len(set(ids)), f"{len(ids)} 个阶段"))

    missing = [(s.id, d) for s in contracts.STAGES for d in s.depends_on
               if d not in contracts.STAGE_BY_ID]
    out.append(_check("依赖均存在", not missing,
                      "无悬空依赖" if not missing else f"悬空：{missing}"))

    # 环检测
    state: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> bool:
        if state.get(node) == 1:
            return False
        if state.get(node) == 2:
            return True
        state[node] = 1
        for dep in contracts.STAGE_BY_ID[node].depends_on:
            if dep in contracts.STAGE_BY_ID and not visit(dep, stack + [node]):
                return False
        state[node] = 2
        return True

    acyclic = all(visit(sid, []) for sid in ids)
    out.append(_check("依赖无环", acyclic, "有向无环" if acyclic else "存在循环依赖"))

    order_ok = True
    for index, sid in enumerate(contracts.STAGE_ORDER):
        for dep in contracts.STAGE_BY_ID[sid].depends_on:
            if dep in contracts.STAGE_ORDER and contracts.STAGE_ORDER.index(dep) > index:
                order_ok = False
    out.append(_check("阶段顺序自洽", order_ok, "依赖都在上游" if order_ok else "依赖晚于自身"))

    human = [s.id for s in contracts.STAGES if s.kind == contracts.HUMAN]
    out.append(_check("人工闸门存在", bool(human), "、".join(human)))

    scripts = [c.script for c in checks.CHECKS]
    out.append(_check("体检脚本已登记", len(scripts) >= 10, f"{len(scripts)} 个检查"))
    return out


def _fixture(root: Path, findings: list, queue: list) -> Path:
    review_dir = root / "07_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "content-review.json").write_text(
        json.dumps({"findings": findings}, ensure_ascii=False), encoding="utf-8")
    (review_dir / "metadata-review.json").write_text(
        json.dumps({"findings": []}, ensure_ascii=False), encoding="utf-8")
    (review_dir / "manual-review-queue.json").write_text(
        json.dumps({"queue_version": "1.0", "items": queue}, ensure_ascii=False), encoding="utf-8")
    (review_dir / "unchecked.json").write_text("[]", encoding="utf-8")
    return root


def check_gates() -> list[dict]:
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        clean = _fixture(Path(tmp) / "clean", [], [])
        result = review.evaluate(clean)
        out.append(_check("闸门用例：干净批次放行", result["passed"],
                          "通过" if result["passed"] else f"误拦：{result['errors'][:1]}"))

    with tempfile.TemporaryDirectory() as tmp:
        blocked = _fixture(
            Path(tmp) / "blocked",
            [{"finding_id": "T1", "unit_id": "U1", "field": "answer",
              "severity": "blocker", "blocking": True, "status": "needs_review", "reason": "测试"}],
            [])
        result = review.evaluate(blocked)
        out.append(_check("闸门用例：阻断项拦截", not result["passed"],
                          "已拦截" if not result["passed"] else "漏放"))

    with tempfile.TemporaryDirectory() as tmp:
        queued = _fixture(
            Path(tmp) / "queued", [],
            [{"finding_id": "T2", "unit_id": "U1", "field": "stem",
              "severity": "minor", "status": "open", "question": "测试"}])
        result = review.evaluate(queued)
        out.append(_check("闸门用例：人工队列拦截", not result["passed"],
                          "已拦截" if not result["passed"] else "漏放"))

    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "missing"
        (missing / "07_review").mkdir(parents=True)
        result = review.evaluate(missing)
        out.append(_check("闸门用例：缺审核文件拦截", not result["passed"],
                          "已拦截" if not result["passed"] else "漏放"))
    return out


def check_batches(skill_root: Path) -> list[dict]:
    root = skill_root / "workflows"
    if not root.is_dir():
        return [_check("批次清单", True, "尚无批次")]
    out = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        path = mf.manifest_path(folder)
        if not path.is_file():
            out.append(_check(f"批次 {folder.name}", False, "缺少 workflow/manifest.json"))
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            out.append(_check(f"批次 {folder.name}", False, f"清单无法解析：{exc}"))
            continue
        missing = [s for s in contracts.STAGE_ORDER if s not in manifest.get("stages", {})]
        status = manifest.get("status", "?")
        out.append(_check(f"批次 {folder.name}", not missing,
                          f"状态 {status}，{len(manifest.get('stages', {}))} 个阶段"
                          + (f"，缺 {missing}" if missing else "")))
    return out


def run(skill_root: Path) -> dict:
    groups = {
        "环境": check_env(),
        "文件": check_files(skill_root),
        "规则": check_rules(skill_root),
        "契约": check_contracts(),
        "闸门": check_gates(),
        "批次": check_batches(skill_root),
    }
    flat = [item for items in groups.values() for item in items]
    failed = [item for item in flat if not item["ok"]]
    return {"groups": groups, "total": len(flat), "failed": failed,
            "ok": not failed}


def print_report(result: dict) -> None:
    for group, items in result["groups"].items():
        bad = [i for i in items if not i["ok"]]
        mark = "✓" if not bad else "✗"
        print(f"\n{mark} {group}（{len(items) - len(bad)}/{len(items)}）")
        for item in items:
            if item["ok"]:
                print(f"    ✓ {item['name']}：{item['detail']}")
            else:
                print(f"    ✗ {item['name']}：{item['detail']}")
                if item["hint"]:
                    print(f"      提示：{item['hint']}")
    print()
    if result["ok"]:
        print(f"自检通过：{result['total']} 项全部正常")
    else:
        print(f"自检未通过：{len(result['failed'])} 项异常，共 {result['total']} 项")
