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

from . import checks, format_gate_core, metadata_policy, upload_payload, wire_format, phonetic_structure, shared_rubric, upload_numbering
from .format_gate_core import (
    DEFERRED_CODES,
    FINAL_PATH,
    _load,
    _options,
    _positive,
    _safe,
    _sha,
    _shape,
    _text,
)
from .label_audit import RULE_FILES as LABEL_RULE_FILES

RULE_VERSION = "final-upload-format-v2"
REPORT_PATH = "07_review/upload-format.json"
RULE_FILES = (Path(__file__), Path(format_gate_core.__file__), Path(upload_payload.__file__),
              Path(upload_payload.label_audit.__file__),
              Path(upload_payload.taxonomy.__file__), Path(wire_format.__file__),
              Path(metadata_policy.__file__), Path(phonetic_structure.__file__),
              Path(shared_rubric.__file__), Path(upload_numbering.__file__),
              Path(__file__).with_name("source_objects.py"),
              Path(__file__).with_name("question_draft.py"), Path(__file__).with_name('table_structure.py'),
               Path(__file__).with_name('question_content.py'), Path(checks.__file__),
               Path(__file__).with_name('findings.py'), Path(__file__).with_name('final_audit.py'),
               Path(__file__).with_name('upload_content.py'),
               *(Path(__file__).resolve().parents[1] / check.script for check in checks.FINAL_FORMAT_CHECKS),
               *LABEL_RULE_FILES)


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

    Presentation findings are observational. Late metadata consistency checks
    keep their declared blocking policy; moving a check never removes it.
    A runner failure is reported explicitly so a missing check is not mistaken
    for a clean format result.
    """
    try:
        result = checks.run_all(Path(__file__).resolve().parents[1], final, inputs,
                                profile="format")
    except Exception as exc:  # a diagnostic must not hide payload validation
        result = {"passed": False, "checks": [], "blocking_failures": [],
                  "warnings": [], "error": f"格式检查执行失败：{exc}"}
    report["format_checks"] = {
        "passed": bool(result.get("passed")),
        "blocking_failures": list(result.get("blocking_failures") or []),
        "warnings": list(result.get("warnings") or []),
        "skipped": list(result.get("skipped") or []),
        "error": result.get("error"),
        "checks": [{key: check.get(key) for key in
                    ("check", "script", "blocking", "passed", "error", "output", "stdout", "stderr", "warnings", "note", "findings")
                    if key in check}
                   for check in (result.get("checks") or []) if isinstance(check, dict)],
    }
    if result.get("error"):
        report["warnings"].append({"code": "FINAL_FORMAT_CHECK_EXECUTION_FAILED",
                                   "message": result["error"]})
    for check in result.get("checks") or []:
        if not check.get("passed", False):
            scoped_errors = [dict(item) for item in (check.get('findings') or [])
                             if isinstance(item, dict) and item.get('blocking')]
            if check.get('blocking') and scoped_errors:
                report['errors'].extend(scoped_errors)
            else:
                report["errors" if check.get("blocking") else "warnings"].append({
                "code": "FINAL_METADATA_CHECK_FAILED" if check.get("blocking") else "FINAL_FORMAT_CHECK_FAILED",
                "check": check.get("check"),
                "script": check.get("script"),
                "message": check.get("error") or check.get("output") or "格式检查未通过",
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
    report.setdefault("findings", []).extend(result.get("findings") or [])
    # A failed aggregate runner cannot silently drop a deferred hard check.
    if result.get("error"):
        for spec in checks.FINAL_FORMAT_CHECKS:
            if spec.blocking:
                # The difficulty rule is pure Python; reuse its existing
                # callable without spawning another process after runner failure.
                # Unknown future hard checks fail closed until a local adapter exists.
                try:
                    import runpy
                    rule = runpy.run_path(str(Path(__file__).resolve().parents[1] / spec.script))
                    data = json.loads(final.read_text(encoding='utf-8-sig'))
                    if Path(spec.script).name == 'check_difficulty.py':
                        problems = rule['problems'](data)
                    elif Path(spec.script).name == 'audit_type_layout.py' and spec.args == ('--content-only',):
                        problems = []
                        for unit in rule['units'](data):
                            if not isinstance(unit, dict):
                                raise ValueError('题型内容检查遇到非对象单元')
                            problems.extend(rule['_check_unit'](unit, content_only=True))
                    else:
                        raise ValueError('最终硬检查缺少本地执行适配：' + spec.script)
                    outcome = {'check': spec.name, 'script': spec.script, 'blocking': True,
                               'passed': not problems, 'error': '\n'.join(problems) or None}
                    if problems and Path(spec.script).name == 'audit_type_layout.py':
                        outcome['findings'] = checks._output_findings(spec, '\n'.join(problems), '', 1)
                except Exception as exc:
                    outcome = {'check': spec.name, 'script': spec.script, 'blocking': True,
                               'passed': False, 'error': str(exc)}
                report["format_checks"]["checks"].append(outcome)
                report.setdefault("findings", []).extend(outcome.get("findings") or [])
                if not outcome.get("passed"):
                    if outcome.get('findings'):
                        report['errors'].extend(outcome['findings'])
                    else:
                        report["errors"].append({"code": "FINAL_METADATA_CHECK_FAILED",
                        "check": spec.name, "script": spec.script,
                        "message": outcome.get("error") or outcome.get("output") or "最终元数据校验未完成"})
    return result


def write_report_file(batch_dir, report: dict) -> None:
    _write_report(_safe(Path(batch_dir) / REPORT_PATH), report)


def run(batch_dir, *, write_report: bool = True) -> dict:
    report = {"passed": False, "status": "failed", "scope": "local_upload_format",
              "rule": RULE_VERSION, "rule_sha256": {p.name: _sha(p.read_bytes()) for p in RULE_FILES},
              "final_path": FINAL_PATH, "final_sha256": None, "input_sha256": {},
              "errors": [], "warnings": [], "deferred": [], "quarantined_units": [],
              "release_ready": False,
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
        # Final checks inspect the complete batch: presentation is advisory,
        # while deferred metadata consistency and payload/content stay blocking.
        inputs = {}
        manifest_path = _safe(root / "workflow/manifest.json")
        if manifest_path.is_file():
            manifest_raw = manifest_path.read_bytes()
            manifest = _load(manifest_raw)
            report["input_sha256"]["workflow/manifest.json"] = _sha(manifest_raw)
            if isinstance(manifest, dict) and isinstance(manifest.get("inputs"), dict):
                inputs = manifest["inputs"]
        _run_final_format_checks(root, final, report, inputs)
        from . import final_audit
        source_report = final_audit.run(root, batch)
        report["final_audit"] = source_report
        report["errors"].extend(source_report["errors"])
        report["warnings"].extend(source_report["warnings"])
        for relative, digest in source_report["source_sha256"].items():
            report["input_sha256"][relative] = digest
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
            elif issue.get("code") == "UNIT_QUARANTINED":
                # 未解除的隔离单元属人工待修产出，不阻断本地 format_gate 执行、不触发运维反复建单；
                # 但明确标记 release_ready=False，真实上传前 prepare-upload 仍做硬拦截。
                report["deferred"].append({**issue, "checked_by": "prepare-upload"})
                uid = str(issue.get("unit_id") or "")
                if uid and uid not in report["quarantined_units"]:
                    report["quarantined_units"].append(uid)
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
        for relative, digest in report.get("final_audit", {}).get("source_sha256", {}).items():
            if _sha(_safe(root / relative).read_bytes()) != digest:
                report["errors"].append({"code": "FINAL_SOURCE_CHANGED", "path": relative,
                                         "message": "最终核对期间来源发生变化"})
        if _safe(final).read_bytes() != raw:
            report["errors"].append({"code": "FINAL_BATCH_CHANGED", "message": "最终批次在检查期间变化，请重新检查"})
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        report["errors"].append({"code": "FORMAT_INPUT_INVALID", "message": str(exc)})
    from .findings import attach
    attach(report, path=REPORT_PATH, stage="format_gate")
    report["passed"] = not report["errors"]
    report["status"] = "passed" if report["passed"] else "failed"
    report["release_ready"] = bool(report["passed"]) and not bool(report["quarantined_units"])
    if write_report:
        try:
            write_report_file(batch_dir, report)
        except (OSError, ValueError) as exc:
            report["errors"].append({"code": "FORMAT_REPORT_WRITE_FAILED", "message": str(exc)})
            report["passed"], report["status"], report["release_ready"] = False, "failed", False
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
