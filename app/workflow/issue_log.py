"""卡点台账：试卷流程每次遇到卡点都记一条，并跟踪它是否已修复。

用户要求（2026-11-10）：插件试卷流程每次遇到卡点就记录下来，同时记录该卡点
有没有被修复；本地维护一份“待修复问题清单”，未修复的放在清单里，已修复的
标注已修复。

落地形态：仓库根目录 `ISSUES/`。
- `ISSUES/open/`  ：未修复（待修复清单）
- `ISSUES/fixed/` ：已修复（status: fixed，含修复方式/提交/验证）

用法（仓库根目录）：

    PYTHONPATH=app python3 -m workflow.issue_log add \
        --batch '【木牍教育】20年安徽中考英语' --stage explanations \
        --title '书面表达思路点拨解析被拒' --check EXPLANATION_SOURCE_UNVERIFIED \
        --why 'source_ref 未剥离【思路点拨】标题' --how '剥离标题并放宽严格模式'

    PYTHONPATH=app python3 -m workflow.issue_log fix --id 2026-11-10-xxx \
        --resolution '剥离标题' --commit bb6e869 --verify 'test_upload_content 47 项通过'

    PYTHONPATH=app python3 -m workflow.issue_log list [--status open|fixed|all]

只写 `ISSUES/` 目录，不触碰批次数据、manifest、收据或上传边界。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

TEMPLATE_VERSION = 1
STATUSES = ("open", "fixed")
# 表头允许的键；解析时只认第一个 `##` 之前的 `- key: value` 行。
HEADER_FIELDS = ("id", "status", "batch", "batch_dir", "stage", "check",
                 "created", "last_seen", "occurrences",
                 "fixed_at", "fix_commit", "verification", "title")
_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[^\s/\\:*?\"<>|]+$")
# 批次内台账：由工作流代码在真正判定卡点时自动写入，测试/离线操作不会污染仓库。
BLOCKERS_FILE = "workflow/blockers.json"
BLOCKERS_VERSION = 1


class IssueError(ValueError):
    """台账操作失败；消息直接面向使用者。"""


def _issues_dir() -> Path:
    override = os.environ.get("WF_ISSUES_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "ISSUES"


def _dirs(root: Path | None = None) -> tuple[Path, Path, Path]:
    base = root or _issues_dir()
    return base, base / "open", base / "fixed"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slug(text: str, limit: int = 48) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    value = re.sub(r"[\s/\\:*?\"<>|\x00-\x1f]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-.")
    return value[:limit].strip("-.") or "issue"


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


def _ensure(base: Path) -> tuple[Path, Path]:
    _, open_dir, fixed_dir = _dirs(base)
    for directory in (base, open_dir, fixed_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return open_dir, fixed_dir


def _check_id(value: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.match(text):
        raise IssueError(f"非法问题编号：{value!r}（须形如 2026-11-10-简短标题，不能含路径分隔符）")
    return text


def _record_path(base: Path, status: str, issue_id: str) -> Path:
    _, open_dir, fixed_dir = _dirs(base)
    return (open_dir if status == "open" else fixed_dir) / (issue_id + ".md")


SECTION_SYMPTOM = "现象（卡点）"
SECTION_CHECK = "具体检查与判定"
SECTION_FIX = "修复记录"


def render(fields: dict) -> str:
    """把一条问题渲染成 Markdown；表头用 `- key: value`，正文分段。"""
    symptom = str(fields.get("symptom") or fields.get("why")
                  or "待补充：具体失败现象（哪条检查、哪道题、什么输出）").strip()
    lines = [f"# 卡点：{_one_line(fields.get('title'))}", ""]
    for key in HEADER_FIELDS:
        value = fields.get(key)
        if value is None or _one_line(value) in ("", "-"):
            continue
        lines.append(f"- {key}: {_one_line(value)}")
    lines += [
        "",
        f"## {SECTION_SYMPTOM}",
        "",
        symptom,
        "",
        f"## {SECTION_CHECK}",
        "",
        f"- 失败检查：{_one_line(fields.get('check')) or '待补充'}",
        f"- 判定口径：{_one_line(fields.get('why')) or '待补充'}",
        f"- 修复方向：{_one_line(fields.get('how')) or '待补充'}",
        "",
        f"## {SECTION_FIX}",
        "",
        str(fields.get("resolution") or "（未修复）").strip(),
        "",
    ]
    return "\n".join(lines)


def sections(text: str) -> dict:
    """把正文按 `## 标题` 切开，返回 {标题: 正文}。"""
    result, current, buffer = {}, None, []
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if current is not None:
                result[current] = "\n".join(buffer).strip()
            current, buffer = match.group(1), []
            continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        result[current] = "\n".join(buffer).strip()
    return result


def parse(text: str) -> dict:
    """解析一条问题记录：表头 + 正文分段（正文原样保留，便于无损改写）。"""
    fields: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        match = re.match(r"^-\s*([a-z_]+)\s*:\s*(.*)$", stripped)
        if match and match.group(1) in HEADER_FIELDS:
            fields[match.group(1)] = match.group(2).strip()
    if "occurrences" in fields:
        try:
            fields["occurrences"] = int(fields["occurrences"])
        except ValueError:
            fields["occurrences"] = 1
    body = sections(text)
    fields["_sections"] = body
    fields["symptom"] = body.get(SECTION_SYMPTOM, "")
    fields["resolution"] = body.get(SECTION_FIX, "")
    check_block = body.get(SECTION_CHECK, "")
    for label, key in (("失败检查", "check"), ("判定口径", "why"), ("修复方向", "how")):
        match = re.search(rf"^-\s*{label}\s*[:：]\s*(.*)$", check_block, re.M)
        if match:
            fields[key] = match.group(1).strip()
    return fields


def _set_header(text: str, updates: dict) -> str:
    """就地改写表头字段，正文与其余行保持逐字不变（表头按固定顺序重排）。"""
    lines = text.splitlines()
    header_end = len(lines)
    for index, line in enumerate(lines):
        if line.strip().startswith("## "):
            header_end = index
            break
    head, rest = lines[:header_end], lines[header_end:]
    values: dict = {}
    extras: list[str] = []
    title = ""
    for line in head:
        if line.startswith("# "):
            title = line
            continue
        match = re.match(r"^-\s*([a-z_]+)\s*:\s*(.*)$", line)
        if match:
            key = match.group(1)
            if key in HEADER_FIELDS:
                values[key] = match.group(2).strip()
            else:
                extras.append(line)  # 未知字段原样保留，不丢用户内容
        elif line.strip():
            extras.append(line)
    values.update({key: _one_line(value) for key, value in updates.items()})
    ordered = [f"- {key}: {values[key]}" for key in HEADER_FIELDS
               if values.get(key) not in (None, "")]
    out = ([title, ""] if title else []) + ordered + extras
    out += [""] if rest else []
    return "\n".join(out + rest).rstrip("\n") + "\n"


def _set_section(text: str, heading: str, body: str) -> str:
    """替换某个 `## 标题` 段的正文；段不存在时追加到文末。"""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"^##\s+{re.escape(heading)}\s*$", line):
            start = index
            break
    if start is None:
        return text.rstrip("\n") + f"\n\n## {heading}\n\n{body.strip()}\n"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    rebuilt = lines[:start + 1] + ["", *body.strip().splitlines(), ""]
    if end < len(lines):
        rebuilt += lines[end:]
    return "\n".join(rebuilt) + "\n"


def _read(path: Path) -> dict:
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IssueError(f"无法读取问题记录：{path.name}：{exc}") from exc


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def find(issue_id: str, root: Path | None = None) -> tuple[str, Path]:
    """按编号定位记录，返回 (status, path)。"""
    base = root or _issues_dir()
    issue_id = _check_id(issue_id)
    for status in STATUSES:
        path = _record_path(base, status, issue_id)
        if path.is_file():
            return status, path
    raise IssueError(f"找不到问题记录：{issue_id}（ISSUES/open 与 ISSUES/fixed 都没有）")


def add(batch: str, stage: str, title: str, *, check: str = "", why: str = "",
        how: str = "", symptom: str = "", batch_dir: str = "",
        root: Path | None = None) -> dict:
    """登记一条卡点。同一批次+阶段+检查重复出现时累加 occurrences，不重复建单。"""
    if not _one_line(title):
        raise IssueError("必须给出问题标题（--title）")
    base = root or _issues_dir()
    open_dir, fixed_dir = _ensure(base)
    check_value = _one_line(check)
    # 同一卡点重复出现：就地累加次数，正文保持原样，避免清单被重复项淹没。
    for path in sorted(open_dir.glob("*.md")):
        existing = _read(path)
        if (existing.get("batch") == _one_line(batch)
                and existing.get("stage") == _one_line(stage)
                and (existing.get("check") or "") == check_value):
            occurrences = int(existing.get("occurrences") or 1) + 1
            _write(path, _set_header(path.read_text(encoding="utf-8"),
                                     {"occurrences": occurrences, "last_seen": _now()}))
            return {"id": path.stem, "status": "open", "occurrences": occurrences,
                    "path": str(path), "deduplicated": True}
    stamp = datetime.now().astimezone()
    issue_id = f"{stamp.date().isoformat()}-{_slug(title)}"
    suffix = 1
    while _record_path(base, "open", issue_id).is_file() or _record_path(base, "fixed", issue_id).is_file():
        suffix += 1
        issue_id = f"{stamp.date().isoformat()}-{_slug(title)}-{suffix}"
    fields = {"id": issue_id, "status": "open", "batch": _one_line(batch),
              "batch_dir": _one_line(batch_dir), "stage": _one_line(stage),
              "check": check_value, "created": _now(), "last_seen": _now(),
              "occurrences": 1, "title": _one_line(title),
              "symptom": symptom, "why": why, "how": how}
    path = _record_path(base, "open", issue_id)
    _write(path, render(fields))
    return {"id": issue_id, "status": "open", "occurrences": 1, "path": str(path),
            "deduplicated": False}


def fix(issue_id: str, *, resolution: str = "", commit: str = "", verify: str = "",
        root: Path | None = None) -> dict:
    """标记已修复：移入 ISSUES/fixed/ 并写入修复方式/提交/验证。"""
    base = root or _issues_dir()
    status, path = find(issue_id, base)
    if status == "fixed":
        raise IssueError(f"问题 {issue_id} 已标记为已修复，不能重复标记")
    fixed_at = _now()
    record = (f"- 修复方式：{_one_line(resolution) or '待补充'}\n"
              f"- 修复提交：{_one_line(commit) or '-'}\n"
              f"- 复验证据：{_one_line(verify) or '待补充（未验证不得标记已修复）'}\n"
              f"- 标记时间：{fixed_at}")
    # 就地改表头与修复记录段，现象/判定口径/修复方向等正文逐字保留。
    text = path.read_text(encoding="utf-8")
    text = _set_header(text, {"status": "fixed", "fixed_at": fixed_at,
                              "fix_commit": _one_line(commit) or "-",
                              "verification": _one_line(verify) or "-"})
    text = _set_section(text, SECTION_FIX, record)
    _ensure(base)
    target = _record_path(base, "fixed", issue_id)
    _write(target, text)
    path.unlink()
    return {"id": issue_id, "status": "fixed", "path": str(target), "fixed_at": fixed_at}


def listing(status: str = "all", root: Path | None = None) -> list[dict]:
    """列出问题记录，按创建时间升序。"""
    if status not in (*STATUSES, "all"):
        raise IssueError(f"非法状态：{status}（只能是 open / fixed / all）")
    base = root or _issues_dir()
    rows = []
    for state in STATUSES:
        if status not in (state, "all"):
            continue
        directory = _dirs(base)[1 if state == "open" else 2]
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            record = _read(path)
            rows.append({"id": path.stem, "status": state, **record})
    rows.sort(key=lambda row: (str(row.get("created") or ""), row["id"]))
    return rows


def summary(root: Path | None = None) -> dict:
    """给状态汇报用的计数：未修复/已修复各多少条。"""
    rows = listing("all", root)
    return {"open": sum(1 for row in rows if row["status"] == "open"),
            "fixed": sum(1 for row in rows if row["status"] == "fixed"),
            "total": len(rows)}


# ---------------------------------------------------------------- 批次内自动台账
#
# 硬规则 24 要求“每次卡点都记录”。光有命令靠人执行不算代码约束，所以在真正判定
# 卡点的地方（self_heal 修复不可用/预算耗尽、supervisor 长时间无进展）由代码调用
# `record_blocker`，写进**该批次自己**的 workflow/blockers.json：
#   - 测试、离线操作、其它批次互不影响，不会污染仓库 ISSUES/；
#   - 同一卡点（阶段+检查+现象）只累加 occurrences，不刷屏；
#   - `issue_log sync` 再把这些按批次汇总进仓库 ISSUES/open/。

def _blocker_key(stage: str, check: str, symptom: str) -> str:
    import hashlib
    digest = hashlib.sha256(" ".join(str(symptom or "").split()).encode("utf-8")).hexdigest()[:12]
    return f"{stage}|{check}|{digest}"


def blockers_path(batch_dir) -> Path:
    return Path(batch_dir) / BLOCKERS_FILE


def load_blockers(batch_dir) -> dict:
    """读取批次内台账；缺失或损坏时返回空结构（只读诊断，不改批次）。"""
    path = blockers_path(batch_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": BLOCKERS_VERSION, "blockers": {}}
    if not isinstance(data, dict) or not isinstance(data.get("blockers"), dict):
        return {"version": BLOCKERS_VERSION, "blockers": {}}
    return data


def record_blocker(batch_dir, stage: str, *, check: str = "", symptom: str = "",
                   why: str = "", how: str = "", error_code: str = "",
                   source: str = "") -> dict | None:
    """把一次真实卡点写进批次内台账；best-effort，绝不因台账失败而中断流程。

    返回写入后的条目（含 occurrences），失败返回 None。调用方不得依赖它成功。
    """
    stage = _one_line(stage)
    if not stage:
        return None
    check_value = _one_line(check) or _one_line(error_code) or "unknown"
    symptom_value = str(symptom or "").strip() or "（未提供现象）"
    batch = Path(batch_dir)
    path = blockers_path(batch)
    try:
        # 不创建批次目录本身（避免造出幽灵批次），只补 workflow/ 子目录。
        if batch.is_symlink() or not batch.is_dir():
            return None
        parent = path.parent
        if parent.is_symlink():
            return None
        parent.mkdir(parents=True, exist_ok=True)
        data = load_blockers(batch)
        key = _blocker_key(stage, check_value, symptom_value)
        entry = data["blockers"].get(key) or {"key": key, "first_seen": _now(), "occurrences": 0}
        entry.update(stage=stage, check=check_value, symptom=symptom_value,
                     why=str(why or "").strip(), how=str(how or "").strip(),
                     error_code=_one_line(error_code), source=_one_line(source),
                     last_seen=_now())
        entry["occurrences"] = int(entry.get("occurrences") or 0) + 1
        data["version"] = BLOCKERS_VERSION
        data["blockers"][key] = entry
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return entry
    except (OSError, ValueError, TypeError):
        return None


def sync(batch_dir, *, root: Path | None = None) -> dict:
    """把某个批次的批次内台账汇总进仓库 ISSUES/open/（同卡点累加，不重复建单）。"""
    batch = Path(batch_dir)
    data = load_blockers(batch)
    batch_name = batch.name
    batch_dir = str(batch)
    created, updated = [], []
    for entry in sorted((data.get("blockers") or {}).values(),
                        key=lambda item: str(item.get("first_seen") or "")):
        if not isinstance(entry, dict) or not entry.get("stage"):
            continue
        result = add(
            batch_name, entry["stage"],
            _sync_title(entry),
            check=str(entry.get("check") or ""),
            symptom=str(entry.get("symptom") or ""),
            why=str(entry.get("why") or ""),
            how=str(entry.get("how") or ""),
            batch_dir=batch_dir, root=root)
        (updated if result.get("deduplicated") else created).append(result["id"])
    return {"batch": batch_name, "created": created, "updated": updated,
            "total": len(created) + len(updated)}


def _sync_title(entry: dict) -> str:
    """标题只要“阶段 + 失败检查”，现象留在正文，避免编号被截断成半句。"""
    stage = str(entry.get("stage") or "")
    check = str(entry.get("check") or "")
    return f"{stage} 卡点 {check}".strip() or "卡点"



def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="试卷流程卡点台账（ISSUES/）")
    sub = parser.add_subparsers(dest="action", required=True)
    add_parser = sub.add_parser("add", help="登记一条卡点（未修复）")
    add_parser.add_argument("--batch", required=True)
    add_parser.add_argument("--stage", required=True)
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--check", default="", help="具体失败检查码/检查名")
    add_parser.add_argument("--symptom", default="", help="具体现象/报错原文")
    add_parser.add_argument("--why", default="", help="为什么判失败（判定口径）")
    add_parser.add_argument("--how", default="", help="修复方向")
    add_parser.add_argument("--batch-dir", default="")
    fix_parser = sub.add_parser("fix", help="标记已修复")
    fix_parser.add_argument("--id", required=True)
    fix_parser.add_argument("--resolution", default="")
    fix_parser.add_argument("--commit", default="")
    fix_parser.add_argument("--verify", default="")
    list_parser = sub.add_parser("list", help="列出问题")
    list_parser.add_argument("--status", default="all", choices=("open", "fixed", "all"))
    list_parser.add_argument("--json", action="store_true")
    show_parser = sub.add_parser("show", help="查看一条问题")
    show_parser.add_argument("--id", required=True)
    sync_parser = sub.add_parser("sync", help="把批次的自动台账汇总进 ISSUES/open/")
    sync_group = sync_parser.add_mutually_exclusive_group(required=True)
    sync_group.add_argument("--batch", help="批次目录，例如 workflows/<批次>")
    sync_group.add_argument("--all", action="store_true", help="汇总 workflows/ 下全部批次")
    args = parser.parse_args(argv)
    try:
        if args.action == "add":
            result = add(args.batch, args.stage, args.title, check=args.check, why=args.why,
                         how=args.how, symptom=args.symptom, batch_dir=args.batch_dir)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.action == "fix":
            print(json.dumps(fix(args.id, resolution=args.resolution, commit=args.commit,
                                 verify=args.verify), ensure_ascii=False))
            return 0
        if args.action == "show":
            status, path = find(args.id)
            print(json.dumps({"status": status, "path": str(path), **_read(path)},
                             ensure_ascii=False, indent=2))
            return 0
        if args.action == "sync":
            targets = []
            if args.batch:
                targets = [Path(args.batch)]
            else:
                root = Path(__file__).resolve().parents[2] / "workflows"
                targets = sorted(p for p in root.glob("*") if (p / "workflow").is_dir()) if root.is_dir() else []
            results = [sync(target) for target in targets]
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0
        rows = listing(args.status)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                print(f"{row['status']:5} {row['id']}  {row.get('batch')} / {row.get('stage')}"
                      f"  {row.get('check') or '-'}  ×{row.get('occurrences') or 1}")
            print(f"合计：未修复 {summary()['open']} 条，已修复 {summary()['fixed']} 条")
        return 0
    except IssueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
