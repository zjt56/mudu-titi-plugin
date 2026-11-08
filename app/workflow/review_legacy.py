"""Read-only whole-plan compatibility for genuinely unchanged retained reviews.

This is deliberately independent of unit projection hashes and sample seed
versions. It never rewrites a plan, task, receipt, transaction or observation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

# Compatibility is an explicit migration for these known prompt contracts, not
# an exemption for future instructions whose semantics have not been checked.
SUPPORTED_CURRENT_CONTRACT = ("blind-context-v11", "review-prompt-v9")


def verify_format_equivalence(batch, relative, old_sha256, current_sha256):
    """Independently replay a committed source-proven migration; deny otherwise.

    Neither a receipt nor its integrity digest certifies equivalence. Actual
    backup/current/source/rule bytes and every reconstructed field must agree.
    No plan, original execution, report or source file is written here.
    """
    from . import format_gate, legacy_format, question_content, self_heal_actions
    try:
        root = format_gate._safe(batch)
        if relative not in legacy_format.BATCH_PATHS or any(
                not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                for digest in (old_sha256, current_sha256)):
            return None
        current_raw = self_heal_actions._path(root, relative).read_bytes()
        if format_gate._sha(current_raw) != current_sha256:
            return None
        migrations = format_gate._safe(root / self_heal_actions.MIGRATIONS)
        if not migrations.is_dir():
            return None
        sources = self_heal_actions._source_hashes(root)
        rules = self_heal_actions._rule_hashes()
        source_raw = {rel: self_heal_actions._path(root, rel).read_bytes()
                      for rel in legacy_format.SOURCE_PATHS}
        for directory in sorted(migrations.iterdir()):
            try:
                format_gate._safe(directory)
                if not directory.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", directory.name):
                    continue
                receipt_rel = f"{self_heal_actions.MIGRATIONS}/{directory.name}/receipt.json"
                receipt_raw = self_heal_actions._path(root, receipt_rel).read_bytes()
                receipt = format_gate._load(receipt_raw)
                sealed = copy.deepcopy(receipt)
                seal = sealed.pop("receipt_sha256", None)
                if (seal != format_gate._sha(self_heal_actions._canonical(sealed))
                        or receipt.get("version") != self_heal_actions.VERSION
                        or receipt.get("rule") != legacy_format.RULE
                        or receipt.get("migration_id") != directory.name
                        or receipt.get("status") != "applied" or receipt.get("passed") is not True
                        or receipt.get("quality_passed") is not False
                        or receipt.get("prepare_upload_required") is not True
                        or any(receipt.get("input_sha256", {}).get(name) != sources.get(name)
                               for name in legacy_format.SOURCE_PATHS)
                        or receipt.get("source_sha256") != sources
                        or receipt.get("rule_sha256") != rules):
                    continue
                files = receipt.get("files")
                if (not isinstance(files, dict) or relative not in files
                        or not set(files) <= set(legacy_format.BATCH_PATHS)
                        or files[relative].get("before_sha256") != old_sha256
                        or files[relative].get("after_sha256") != current_sha256):
                    continue
                replay_changes, verified_bytes = [], {}
                for name, entry in files.items():
                    prefix = f"{self_heal_actions.MIGRATIONS}/{directory.name}"
                    backup = f"{prefix}/original/{name}"
                    candidate_path = f"{prefix}/candidates/{name}"
                    if (entry.get("backup_path") != backup
                            or entry.get("candidate_path") != candidate_path
                            or receipt.get("rollback_backups", {}).get(name) != backup):
                        raise ValueError("Migration backup path mismatch")
                    original_raw = self_heal_actions._path(root, backup).read_bytes()
                    actual_raw = self_heal_actions._path(root, name).read_bytes()
                    if (format_gate._sha(original_raw) != entry.get("before_sha256")
                            or format_gate._sha(actual_raw) != entry.get("after_sha256")
                            or self_heal_actions._path(root, candidate_path).read_bytes() != actual_raw
                            or receipt.get("input_sha256", {}).get(name) != entry.get("before_sha256")):
                        raise ValueError("Migration raw hashes do not match")
                    old = format_gate._load(original_raw)
                    current = format_gate._load(actual_raw)
                    replay = legacy_format.normalize_candidate(old,
                        source=source_raw[legacy_format.SOURCE_PATHS[0]].decode("utf-8-sig"),
                        structure=format_gate._load(source_raw[legacy_format.SOURCE_PATHS[1]]),
                        confirmed=format_gate._load(source_raw[legacy_format.SOURCE_PATHS[2]]))
                    if (not replay.get("passed") or not replay.get("changes")
                            or self_heal_actions._canonical(replay["candidate"]) != actual_raw
                            or not question_content.audit_batch_source(root, old).get("passed")
                            or not question_content.audit_batch_source(root, current).get("passed")):
                        raise ValueError("Migration candidate is not exactly source equivalent")
                    verified_bytes.update({name: actual_raw, backup: original_raw, candidate_path: actual_raw})
                    replay_changes.extend({"file": name, **change} for change in replay["changes"])
                # Proof metadata is also reconstructed instead of blindly trusted.
                order = lambda row: self_heal_actions._canonical(row)
                if sorted(map(order, replay_changes)) != sorted(map(order, receipt.get("changes", []))):
                    raise ValueError("Migration changes were forged")
                final = format_gate._load(self_heal_actions._path(root, format_gate.FINAL_PATH).read_bytes())
                if not legacy_format._local_format(root, final).get("passed"):
                    raise ValueError("Migration final format is no longer valid")
                if (self_heal_actions._source_hashes(root) != sources
                        or self_heal_actions._rule_hashes() != rules
                        or any(self_heal_actions._path(root, name).read_bytes() != raw
                               for name, raw in verified_bytes.items())
                        or self_heal_actions._path(root, relative).read_bytes() != current_raw
                        or self_heal_actions._path(root, receipt_rel).read_bytes() != receipt_raw):
                    raise ValueError("Migration inputs changed during verification")
                return {"mode": "source-proven-format-equivalence/v1",
                        "receipt_path": receipt_rel, "rule": legacy_format.RULE,
                        "before_sha256": old_sha256, "after_sha256": current_sha256,
                        "backup_sha256_verified": True, "source_sha256_verified": True,
                        "candidate_recomputed": True, "quality_approval_granted": False}
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
                continue
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        pass
    return None


def _file(base, relative):
    from .agent_tasks import _safe_path
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Invalid legacy input path")
    return _safe_path(base, relative)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _supplied(prompt):
    if not isinstance(prompt, str):
        raise ValueError("Legacy frozen prompt missing")
    content = prompt.split("BEGIN SUPPLIED CONTENT", 1)[1].rsplit("END SUPPLIED CONTENT", 1)[0]
    # Both retained prompt envelopes have an optional fixed label before JSON.
    start = content.find("{")
    if start < 0:
        raise ValueError("Legacy supplied context missing")
    value = json.loads(content[start:])
    if not isinstance(value, dict):
        raise ValueError("Legacy supplied context is not an object")
    return value


def compatible_plan(batch_dir, current, payload):
    """Return compatibility evidence, or a denial; never grant quality approval.

    Old private extraction bindings can contain moved machine-local paths. We
    do not claim those raw private files are current: the complete old *plan*
    bytes, topology and original execution chain are the authority here.
    """
    from . import review, review_rounds
    batch = Path(batch_dir)
    info = {"accepted": False, "mode": "whole-plan-read-only/v1"}
    try:
        if (review_rounds.CONTEXT_VERSION, review_rounds.PROMPT_VERSION) != SUPPORTED_CURRENT_CONTRACT:
            raise ValueError("New prompt/context contract requires fresh review")
        if not isinstance(payload, dict) or set(payload) != {"plan", "rounds"}:
            raise ValueError("Legacy payload must contain exactly plan and rounds")
        old, records = payload["plan"], payload["rounds"]
        if not isinstance(old, dict) or old.get("protocol") != current.get("protocol"):
            raise ValueError("Legacy review protocol changed")
        hashes = old.get("input_hashes")
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError("Legacy plan has no input hashes")
        if old.get("input_binding") != review._digest({"input_hashes": hashes, "questions": old.get("questions")}):
            raise ValueError("Legacy plan input binding is invalid")
        if old.get("units") != current.get("units") or old.get("questions") != current.get("questions"):
            raise ValueError("Legacy unit/question topology changed")
        if not old.get("units") or len(set(old["units"])) != len(old["units"]):
            raise ValueError("Legacy topology is empty or duplicated")
        root = Path(review.__file__).resolve().parents[1]
        equivalent = []
        for name, expected in hashes.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Invalid legacy SHA256")
            rule = name.startswith("rules:")
            path = _file(root if rule else batch, name[6:] if rule else name)
            actual = _sha(path)
            if actual != expected:
                proof = None if rule else verify_format_equivalence(batch, name, expected, actual)
                if not proof:
                    raise ValueError("Legacy input/rule bytes changed: " + name)
                equivalent.append({"path": name, "proof": proof})
        added = sorted(set(current["input_hashes"]) - set(hashes))
        if any(not name.startswith("rules:") for name in added):
            raise ValueError("New non-rule input requires fresh review")
        # Reconstruct the actual current pool, not merely its new seed's sample.
        pool = review.round_sample_pool(batch)
        keys = [(q["unit_id"], q["number"]) for q in pool]
        sample = old.get("sample")
        if not keys or len(set(keys)) != len(keys) or not isinstance(sample, list):
            raise ValueError("Invalid review sample pool")
        if any(not isinstance(q, dict) or set(q) != {"unit_id", "number"} for q in sample):
            raise ValueError("Invalid legacy sample target")
        selected = [(q["unit_id"], q["number"]) for q in sample]
        if (len(selected) < (len(keys) + 4) // 5 or len(set(selected)) != len(selected)
                or not set(selected) <= set(keys)):
            raise ValueError("Legacy sample must be unique, legal and cover at least 20%")
        if (not isinstance(records, list) or len(records) != len(review.ROUND_IDS)
                or {r.get("round_id") for r in records if isinstance(r, dict)} != set(review.ROUND_IDS)):
            raise ValueError("Whole-plan compatibility requires all original rounds")
        rules = {}
        for name in added:
            path = _file(root, name[6:])
            if _sha(path) != current["input_hashes"][name]:
                raise ValueError("Current newly registered rule hash is stale")
            rules[name] = (path, path.read_text(encoding="utf-8-sig"))
        executions, task_ids = set(), []
        for record in records:
            if record.get("reuse"):
                raise ValueError("Whole-plan compatibility requires original executions, not rebased references")
            # _round_checks normalizes sample checks in memory: protect caller data.
            review._verify_round_sources(batch, old, record)
            review._round_checks(copy.deepcopy(record), record["round_id"], old, batch, lenient=True)
            ids = [p["execution_id"] for p in record.get("parts", [])] or [record["execution_id"]]
            identities = set(ids) | {record["execution_id"]}
            if executions & identities:
                raise ValueError("Legacy rounds share an execution")
            executions.update(identities)
            for task_id in ids:
                binding = review._load(_file(batch, "workflow/review-bindings/" + task_id + ".json"))
                public = review._load(_file(batch, "workflow/review-tasks/" + task_id + "/task.json"))
                receipt = review._load(_file(batch, "workflow/review-bindings/" + task_id + ".receipt.json"))
                if public != binding.get("task") or not isinstance(public, dict):
                    raise ValueError("Legacy public task differs from its frozen private task")
                if not isinstance(receipt, dict) or not isinstance(receipt.get("result_digest"), str):
                    raise ValueError("Legacy executed task receipt missing")
                # The source verifier already binds receipt.result_digest to the
                # completed partition or final transaction and retained response.
                supplied = _supplied(public["prompt"]) if rules else {}
                for name, (path, text) in rules.items():
                    if (binding.get("input_hashes", {}).get(str(path.resolve())) != current["input_hashes"][name]
                            or supplied.get(name[6:]) != text):
                        raise ValueError("New rule was not fully frozen in every original task: " + name)
                task_ids.append(task_id)
        info.update(accepted=True, reason="原计划实际输入与规则未变；完整原执行链及原抽样仍有效，保留原计划和执行标识。",
                    original_input_binding=old["input_binding"], current_input_binding=current["input_binding"],
                    verified_input_count=len(hashes), verified_task_ids=task_ids,
                    registered_existing_rules=added, retired_unchanged_rules=sorted(set(hashes) - set(current["input_hashes"])),
                    sample_count=len(sample), sample_pool_count=len(pool), sample_preserved=True,
                    unit_projection_hashes_used=False, format_equivalence=equivalent,
                    private_raw_inputs_claimed_unchanged=False)
    except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError) as exc:
        info["reason"] = str(exc)
    return info
