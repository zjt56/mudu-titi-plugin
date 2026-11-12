"""Final-batch offline format gate; never prepare, submit, relabel or edit input.

``passed`` certifies local format only. Live catalogs, deployed media and frozen
request verification remain the responsibility of the real prepare-upload flow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from . import checks, metadata_policy, upload_payload, wire_format, phonetic_structure, shared_rubric, upload_numbering
from .label_audit import RULE_FILES as LABEL_RULE_FILES

RULE_VERSION = "final-upload-format-v1"
FINAL_PATH = "06_batch/batch_meta.json"
REPORT_PATH = "07_review/upload-format.json"
RULE_FILES = (Path(__file__), Path(upload_payload.__file__),
              Path(upload_payload.label_audit.__file__),
              Path(upload_payload.taxonomy.__file__), Path(wire_format.__file__),
              Path(metadata_policy.__file__), Path(phonetic_structure.__file__),
              Path(shared_rubric.__file__), Path(upload_numbering.__file__),
              Path(__file__).with_name("source_objects.py"),
              Path(__file__).with_name("question_draft.py"), *LABEL_RULE_FILES)
DEFERRED_CODES = frozenset({"CATALOG_REQUIRED", "TEXTBOOK_CATALOG_UNSUPPORTED",
                            "TEXTBOOK_KNOWLEDGE_INVALID"})


def _safe(path):
    if ".." in Path(path).parts:
        raise ValueError("不允许包含父目录跳转的路径")
    path = Path(os.path.abspath(path))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"不允许软链接路径：{path}")
    return path


def _load(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"JSON 重复键：{key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"JSON 不允许非有限数值：{value}")

    def number(value):
        result = float(value)
        return result if math.isfinite(result) else constant(value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=constant, parse_float=number)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _positive(value):
    return type(value) is int and value > 0


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _shape(batch):
    """Check final wire shape before the builder can default/convert values."""
    errors = []
    ids, units = {}, {}

    def error(code, message, path):
        errors.append({"code": code, "message": message, "path": path})

    def unique(value, seen, code, path):
        if value in seen:
            error(code, f"重复身份 {value}（首次位于 {seen[value]}）", path)
        else:
            seen[value] = path

    def visit(item, path, child=False):
        if not isinstance(item, dict):
            error("ITEM_INVALID", "题目/子题必须是对象", path)
            return
        required = ("number", "stem") if child else ("id", "unit_kind", "number", "numbers", "stem", "knowledge")
        for key in required:
            if key not in item:
                error("SCHEMA_REQUIRED", f"缺少必填字段 {key}", path)
        if not child and not any(key in item for key in ("type", "question_type")):
            error("SCHEMA_REQUIRED", "缺少必填字段 type/question_type", path)
        for key in ("id", "unit_id"):
            if key in item:
                if not _text(item[key]):
                    error("IDENTITY_INVALID", f"{key} 必须是非空字符串", path)
                else:
                    unique(item[key], ids if key == "id" else units,
                           "QUESTION_ID_DUPLICATE" if key == "id" else "UNIT_ID_DUPLICATE", path)
        kind = item.get("unit_kind")
        children = item.get("subquestions", [])
        if "number" in item and not _positive(item["number"]):
            error("QUESTION_NUMBER_INVALID", "number 必须是正整数", path)
        if not child and "numbers" in item:
            ns = item["numbers"]
            if (not isinstance(ns, list) or not ns or any(not _positive(n) for n in ns)
                    or len(ns) != len(set(n for n in ns if type(n) is int))):
                error("QUESTION_NUMBERS_INVALID", "numbers 必须是唯一正整数数组", path)
            elif kind == "standalone" and ns != [item.get("number")]:
                error("QUESTION_NUMBER_COVERAGE", "独立题 numbers 必须等于 [number]", path)
            elif kind == "group" and item.get("number") != min(ns):
                error("QUESTION_NUMBER_COVERAGE", "题组 number 必须为首个子题号", path)
        for key in ("stem", "answer", "explanation", "articleSummary"):
            if key in item and not isinstance(item[key], str):
                error("TEXT_FIELD_INVALID", f"{key} 必须是字符串；缺失内容可保留原占位", path)
        if "options" in item and (not isinstance(item["options"], list)
                                  or any(not isinstance(o, str) for o in item["options"])):
            error("OPTIONS_INVALID", "最终 options 必须是字符串数组", path)
        for key in ("knowledgeIds", "knowledge_ids"):
            if key in item:
                values = item[key]
                if (not isinstance(values, list) or any(not _text(v) for v in values)
                        or len(values) != len(set(v for v in values if isinstance(v, str)))):
                    error("KNOWLEDGE_IDS_INVALID", f"{key} 必须是无重复的字符串 ID 数组", path)
        if "knowledge_version_id" in item and not _text(item["knowledge_version_id"]):
            error("KNOWLEDGE_VERSION_INVALID", "knowledge_version_id 必须是非空字符串", path)
        mappings = item.get("textbookMappings", item.get("textbook_mappings"))
        if isinstance(mappings, list):
            for mapping in mappings:
                if isinstance(mapping, dict) and not _text(mapping.get("knowledgeVersionId")):
                    error("TEXTBOOK_VERSION_INVALID", "教材映射必须有字符串 knowledgeVersionId", path)
        if isinstance(children, list):
            for index, sub in enumerate(children):
                visit(sub, f"{path}.subquestions[{index}]", True)

    if isinstance(batch, dict) and isinstance(batch.get("items"), list):
        for index, item in enumerate(batch["items"]):
            visit(item, f"items[{index}]")
        errors.extend(upload_payload.identity_errors(batch["items"], legacy_numbers=True))
    return errors


def _options(root, batch, report):
    options = {}
    if not isinstance(batch, dict):
        return options
    need_id = "batch_id" not in batch and "id" not in batch
    need_name = "name" not in batch and "title" not in batch
    if not (need_id or need_name):
        return options
    path = _safe(root / "workflow/manifest.json")
    if not path.exists():
        return options
    raw = path.read_bytes()
    manifest = _load(raw)
    report["input_sha256"]["workflow/manifest.json"] = _sha(raw)
    if not isinstance(manifest, dict):
        raise ValueError("manifest 必须是对象")
    if need_id and _text(manifest.get("workflow_id")):
        options["batch_id"] = manifest["workflow_id"]
    meta = manifest.get("meta", {})
    if need_name and isinstance(meta, dict):
        for key in ("paper_title", "name", "title"):
            if _text(meta.get(key)):
                options["name"] = meta[key]
                break
    return options


def _local_attachments(root, batch, report):
    """Bind local files to builder owners without rewriting batch or payload URLs.

    The builder reports URL failures by owner only. Defer only an unambiguous
    owner's complete set of rejected URLs, never a guessed issue in a mixed set.
    """
    owners = {}
    files = {}

    def visit(item, uid, location, child=False):
        if not isinstance(item, dict):
            return
        state = {"rejected": 0, "local": 0, "paths": []}
        owners.setdefault(uid, []).append(state)
        attachments = item.get("attachments", [])
        for index, attachment in enumerate(attachments if isinstance(attachments, list) else []):
            if not isinstance(attachment, dict):
                continue
            url = attachment.get("url")
            if upload_payload._media_path(url):
                continue
            state["rejected"] += 1
            # Non-relative URLs stay the builder's responsibility and block.
            if not isinstance(url, str) or not url or url.startswith("/"):
                continue
            path = f"{location}.attachments[{index}]"
            try:
                parts = urlsplit(url)
                if (parts.scheme or parts.netloc or parts.query or parts.fragment
                        or any(c in url for c in ("\\", "%", "?", "#", ":"))
                        or any(ord(c) < 32 or ord(c) == 127 for c in url)
                        or any(part in ("", ".", "..") for part in url.split("/"))):
                    raise ValueError("附件必须是安全的批次内相对文件路径")
                target = _safe(root / url)
                target.relative_to(root)
                if not target.is_file():
                    raise ValueError("本地附件文件不存在或不是普通文件")
                digest = _sha(target.read_bytes())
                previous = files.get(url)
                if previous is not None and previous != digest:
                    raise ValueError("本地附件在检查期间变化")
                files[url] = digest
                report["input_sha256"][url] = digest
                state["local"] += 1
                state["paths"].append(path)
            except (OSError, ValueError) as exc:
                report["errors"].append({"code": "LOCAL_ATTACHMENT_INVALID", "unit_id": uid,
                                         "path": path, "message": str(exc)})
        children = item.get("subquestions", [])
        # Match the builder's traversal, including rejection of nested groups.
        if isinstance(children, list) and not (child and (item.get("unit_kind") == "group" or children)):
            for index, sub in enumerate(children, 1):
                number = sub.get("number", index) if isinstance(sub, dict) else index
                visit(sub, f"{uid}/{number}", f"{location}.subquestions[{index - 1}]", True)

    items = batch.get("items", []) if isinstance(batch, dict) else []
    for index, item in enumerate(items if isinstance(items, list) else []):
        uid = str(item.get("id") or item.get("unit_id") or index) if isinstance(item, dict) else str(index)
        visit(item, uid, f"items[{index}]")
    return owners, files


def _defer_local_urls(owners, errors):
    counts = {}
    for issue in errors:
        if issue.get("code") == "ATTACHMENT_URL_INVALID":
            uid = issue.get("unit_id")
            counts[uid] = counts.get(uid, 0) + 1
    return {uid: states[0] for uid, states in owners.items()
            if len(states) == 1 and states[0]["local"] > 0
            and states[0]["local"] == states[0]["rejected"] == counts.get(uid)}


def _write_report(path, report):
    # Escape non-ASCII so reports and CLI output remain portable across Windows
    # code pages; JSON consumers recover the original Unicode text.
    raw = (json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2,
                      allow_nan=False) + "\n").encode("utf-8")
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == raw:
        return
    fd, temporary = tempfile.mkstemp(prefix=".upload-format-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _run_final_format_checks(root, final, report, inputs):
    """Run the format-only checks at the final upload boundary.

    These checks are observational: their output is retained as structured
    warnings and never changes the content/identity result from build_payload.
    A runner failure is reported explicitly so a missing check is not mistaken
    for a clean format result.
    """
    try:
        result = checks.run_all(Path(__file__).resolve().parents[1], final, inputs,
                                profile="format")
    except Exception as exc:  # a diagnostic must not hide payload validation
        result = {"passed": False, "checks": [], "blocking_failures": [],
                  "warnings": [], "error": f"格式检查执行失败：{exc}"}
    # Do not persist raw subprocess output: it may contain undecodable bytes
    # represented as U+FFFD on Windows and is not needed for the final report.
    report["format_checks"] = {
        "passed": bool(result.get("passed")),
        "blocking_failures": list(result.get("blocking_failures") or []),
        "warnings": list(result.get("warnings") or []),
        "skipped": list(result.get("skipped") or []),
        "error": result.get("error"),
        "checks": [{key: check.get(key) for key in
                    ("check", "script", "blocking", "passed", "error", "warnings", "note")
                    if key in check}
                   for check in (result.get("checks") or []) if isinstance(check, dict)],
    }
    if result.get("error"):
        report["warnings"].append({"code": "FINAL_FORMAT_CHECK_EXECUTION_FAILED",
                                   "message": result["error"]})
    for check in result.get("checks") or []:
        if not check.get("passed", False):
            report["warnings"].append({
                "code": "FINAL_FORMAT_CHECK_FAILED",
                "check": check.get("check"),
                "script": check.get("script"),
                "message": check.get("error") or "格式检查未通过",
            })
        for warning in check.get("warnings") or []:
            report["warnings"].append({"code": "FINAL_FORMAT_CHECK_WARNING",
                                       "check": check.get("check"),
                                       "message": warning})
    for name in result.get("warnings") or []:
        if not any(row.get("check") == name for row in report["warnings"]
                   if isinstance(row, dict)):
            report["warnings"].append({"code": "FINAL_FORMAT_CHECK_WARNING",
                                       "check": name, "message": name})
    return result


def run(batch_dir) -> dict:
    report = {"passed": False, "status": "failed", "scope": "local_upload_format",
              "rule": RULE_VERSION, "rule_sha256": {p.name: _sha(p.read_bytes()) for p in RULE_FILES},
              "final_path": FINAL_PATH, "final_sha256": None, "input_sha256": {},
              "errors": [], "warnings": [], "deferred": [],
              "prepare_upload_required": True}
    try:
        root = _safe(batch_dir)
        final = _safe(root / FINAL_PATH)
        raw = final.read_bytes()
        report["final_sha256"] = report["batch_sha256"] = report["source_sha256"] = _sha(raw)
        report["input_sha256"][FINAL_PATH] = _sha(raw)
        batch = _load(raw)
        report["errors"].extend(_shape(batch))
        options = _options(root, batch, report)
        # Format-only checks are intentionally late-bound here.  They inspect
        # the final batch and contribute diagnostics only; payload/content
        # errors below retain their existing blocking semantics.
        inputs = {}
        manifest_path = _safe(root / "workflow/manifest.json")
        if manifest_path.is_file():
            manifest_raw = manifest_path.read_bytes()
            manifest = _load(manifest_raw)
            report["input_sha256"]["workflow/manifest.json"] = _sha(manifest_raw)
            if isinstance(manifest, dict) and isinstance(manifest.get("inputs"), dict):
                inputs = manifest["inputs"]
        _run_final_format_checks(root, final, report, inputs)
        report["dry_run_options"] = options
        owners, local_files = _local_attachments(root, batch, report)
        # This is the real, read-only mapping API. No client/prepare imports.
        built = upload_payload.build_payload(batch, catalog=None, batch_dir=root, **options)
        deferred_owners = _defer_local_urls(owners, built["errors"])
        for issue in built["errors"]:
            if (issue.get("code") == "ATTACHMENT_URL_INVALID"
                    and issue.get("unit_id") in deferred_owners):
                report["deferred"].append({**issue, "code": "MEDIA_DEPLOYMENT_REQUIRED",
                    "source_code": "ATTACHMENT_URL_INVALID", "checked_by": "prepare-upload",
                    "local_attachment_paths": deferred_owners[issue["unit_id"]]["paths"],
                    "message": "本地附件路径与文件已校验并记录摘要；真实上传前仍须部署媒体。"})
            elif issue.get("code") in DEFERRED_CODES:
                report["deferred"].append({**issue, "checked_by": "prepare-upload"})
            else:
                report["errors"].append(issue)
        format_warnings = report.get("warnings", [])
        report["warnings"] = format_warnings + built["warnings"]
        report["summary"] = built["summary"]
        report["deferred"].append({"code": "REMOTE_MEDIA_AND_FREEZE_VERIFICATION",
            "message": "远端媒体部署、可达性和冻结一致性仍由真实 prepare-upload/verify 检查；本报告不证明线上就绪。",
            "checked_by": "prepare-upload/verify"})
        if report["errors"] and all(issue.get("code") in {"MISSING_STEM", "MATERIAL_BLOCKS_INVALID"} for issue in report["errors"]):
            from . import legacy_format
            candidate = legacy_format.prepare(root)
            report["source_proven_format_candidate"] = {
                "available": bool(candidate.get("passed")), "installed": False,
                "changed_units": sorted({str(change.get("unit_id")) for change in candidate.get("changes", [])}),
                "change_count": len(candidate.get("changes", [])),
                "refused": candidate.get("refused", []),
                "note": "仅提供原文可证明的格式兼容候选；未修改批次、未改变审核质量结论。"}
        for url, digest in local_files.items():
            try:
                unchanged = _sha(_safe(root / url).read_bytes()) == digest
            except (OSError, ValueError):
                unchanged = False
            if not unchanged:
                report["errors"].append({"code": "LOCAL_ATTACHMENT_CHANGED", "path": url,
                                         "message": "本地附件在检查期间变化，请重新检查"})
        if _safe(final).read_bytes() != raw:
            report["errors"].append({"code": "FINAL_BATCH_CHANGED", "message": "最终批次在检查期间变化，请重新检查"})
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        report["errors"].append({"code": "FORMAT_INPUT_INVALID", "message": str(exc)})
    report["passed"] = not report["errors"]
    report["status"] = "passed" if report["passed"] else "failed"
    try:
        _write_report(_safe(Path(batch_dir) / REPORT_PATH), report)
    except (OSError, ValueError) as exc:
        report["errors"].append({"code": "FORMAT_REPORT_WRITE_FAILED", "message": str(exc)})
        report["passed"], report["status"] = False, "failed"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.batch_dir)
    print(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
