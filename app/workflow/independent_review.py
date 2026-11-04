"""Independent read-only three-level review and subquestion sampling gate."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from pathlib import Path

VERSION = "1.0"
LEVELS = ("content", "metadata", "manual")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def make_plan(batch: dict, evidence_files: dict, rule_version: str, seed: bytes) -> dict:
    """Snapshot batch and evidence, and sample 20% of individual questions."""
    if not isinstance(batch, dict) or not isinstance(batch.get("items"), list) or not batch["items"]:
        raise ValueError("batch.items must be a nonempty list")
    if not _nonempty(rule_version) or not isinstance(seed, bytes) or len(seed) < 16:
        raise ValueError("rule version and a seed of at least 16 bytes are required")
    if not isinstance(evidence_files, dict) or not evidence_files:
        raise ValueError("evidence files are required")
    evidence = {}
    for name, path in evidence_files.items():
        if not _nonempty(name):
            raise ValueError("invalid evidence name")
        try:
            evidence[name] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("evidence file unavailable: " + name) from exc
    units, questions = set(), []
    for item in batch["items"]:
        if not isinstance(item, dict) or not _nonempty(item.get("id")) or item["id"] in units:
            raise ValueError("missing or duplicate unit id")
        uid = item["id"]
        units.add(uid)
        subs = item.get("subquestions", [])
        if not isinstance(subs, list) or (item.get("unit_kind") == "group" and not subs):
            raise ValueError("invalid or missing subquestions: " + uid)
        if subs:
            numbers = set()
            for sub in subs:
                number = sub.get("number") if isinstance(sub, dict) else None
                if not isinstance(number, (int, str)) or isinstance(number, bool) or not str(number).strip() or str(number) in numbers:
                    raise ValueError("missing or duplicate subquestion number: " + uid)
                numbers.add(str(number))
                questions.append([uid, str(number)])
        else:
            questions.append([uid, None])
    snapshot = {"version": VERSION, "rule_version": rule_version, "batch_sha256": _digest(batch),
                "evidence_sha256": evidence, "questions": questions}
    snapshot_id = _digest(snapshot)
    ranked = sorted(questions, key=lambda q: hmac.new(seed, _canonical([snapshot_id, q]), hashlib.sha256).digest())
    return {**snapshot, "snapshot_id": snapshot_id, "sample": ranked[:math.ceil(len(questions) / 5)],
            "seed_commitment": hashlib.sha256(seed).hexdigest()}


def sample_commitment(plan: dict) -> str:
    """Return the value reviewers must bind into each signed record."""
    return _digest(plan["sample"])


def sign_record(record: dict, key: bytes) -> dict:
    """Sign an independently authored record; keys must not be stored in a batch."""
    if not isinstance(record, dict) or "signature" in record or not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("record and a key of at least 32 bytes are required")
    return {**record, "signature": hmac.new(key, _canonical(record), hashlib.sha256).hexdigest()}


def evaluate(batch: dict, evidence_files: dict, rule_version: str, seed: bytes,
             plan: dict, records: list, trusted_reviewers: dict) -> dict:
    """Check all coverage and signatures. This function does not perform review."""
    errors = []
    def fail(code, detail):
        errors.append({"code": code, "detail": detail})
    try:
        current = make_plan(batch, evidence_files, rule_version, seed)
    except (ValueError, TypeError, OverflowError) as exc:
        return {"passed": False, "errors": [{"code": "INPUT_INVALID", "detail": str(exc)}]}
    if plan != current:
        fail("SNAPSHOT_MISMATCH", "batch, evidence, rules, seed or sample changed")
    if not isinstance(records, list) or not isinstance(trusted_reviewers, dict):
        return {"passed": False, "errors": errors + [{"code": "RECORDS_INVALID", "detail": "records or trust store invalid"}]}
    required = {level: {tuple(q) for q in current["questions"]} for level in LEVELS}
    required["sample"] = {tuple(q) for q in current["sample"]}
    seen = {level: set() for level in required}
    actors = {level: set() for level in required}
    for rec in records:
        if not isinstance(rec, dict):
            fail("RECORD_INVALID", "record is not an object")
            continue
        actor, level = rec.get("actor"), rec.get("level")
        if not _nonempty(actor) or not isinstance(level, str) or level not in required:
            fail("RECORD_INVALID", "invalid actor or level")
            continue
        trust = trusted_reviewers.get(actor)
        if not isinstance(trust, dict) or trust.get("level") != level or trust.get("kind") not in ("human", "ai"):
            fail("UNTRUSTED_REVIEWER", actor)
            continue
        key, signature = trust.get("key"), rec.get("signature")
        if not isinstance(key, bytes) or len(key) < 32 or not isinstance(signature, str):
            fail("SIGNATURE_INVALID", actor)
            continue
        try:
            expected = hmac.new(key, _canonical({k: v for k, v in rec.items() if k != "signature"}), hashlib.sha256).hexdigest()
            valid = hmac.compare_digest(expected, signature)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            fail("SIGNATURE_INVALID", actor)
            continue
        q = rec.get("question")
        if not isinstance(q, list) or len(q) != 2 or not isinstance(q[0], str) or (q[1] is not None and not isinstance(q[1], str)):
            fail("RECORD_INVALID", "invalid question")
            continue
        q = tuple(q)
        ev = rec.get("evidence")
        if (rec.get("snapshot_id") != current["snapshot_id"] or rec.get("sample_commitment") != _digest(current["sample"]) or
                rec.get("status") != "reviewed" or type(rec.get("requires_human")) is not bool or (rec.get("requires_human") and trust.get("kind") != "human") or not _nonempty(rec.get("note")) or
                not isinstance(ev, dict) or not isinstance(ev.get("name"), str) or ev.get("name") not in current["evidence_sha256"] or not _nonempty(ev.get("locator")) or
                q not in required[level] or q in seen[level]):
            fail("RECORD_INVALID", actor + ": incomplete, duplicate, stale, or unresolved")
            continue
        seen[level].add(q)
        actors[level].add(actor)
    for level in required:
        if seen[level] != required[level]:
            fail("COVERAGE_MISSING", level + ": " + str(len(required[level] - seen[level])))
    for i, level in enumerate(required):
        for other in list(required)[i + 1:]:
            if actors[level] & actors[other]:
                fail("REVIEWER_NOT_INDEPENDENT", level + "/" + other)
    keys = [v.get("key") for v in trusted_reviewers.values() if isinstance(v, dict) and isinstance(v.get("key"), bytes)]
    if len(keys) != len(set(keys)):
        fail("REVIEWER_KEY_REUSED", "distinct reviewer keys required")
    return {"passed": not errors, "errors": errors, "snapshot_id": current["snapshot_id"],
            "sample": current["sample"], "counts": {k: {"required": len(required[k]), "reviewed": len(seen[k])} for k in required}}


def evaluate_directory(batch_dir, trusted_config_path) -> dict:
    """Load untrusted batch records and separately provisioned trusted configuration."""
    try:
        root = Path(batch_dir).resolve(strict=True)
        config_path = Path(trusted_config_path).resolve(strict=True)
        if config_path.is_relative_to(root):
            raise ValueError("trusted configuration must be outside the batch directory")
        def load(path):
            return json.loads(path.read_text(encoding="utf-8-sig"))
        config = load(config_path)
        if not isinstance(config, dict) or config.get("version") != VERSION:
            raise ValueError("unsupported trusted configuration version")
        batch_path = (root / config["batch_file"]).resolve(strict=True)
        if not batch_path.is_relative_to(root):
            raise ValueError("batch file must be inside the batch directory")
        evidence = config["evidence_files"]
        if not isinstance(evidence, dict):
            raise ValueError("invalid evidence configuration")
        evidence = {k: str((config_path.parent / v).resolve(strict=True)) for k, v in evidence.items()}
        reviewers = {}
        for actor, data in config["reviewers"].items():
            reviewers[actor] = {**data, "key": bytes.fromhex(data["key_hex"])}
        seed = bytes.fromhex(config["seed_hex"])
        return evaluate(load(batch_path), evidence, config["rule_version"], seed,
                        load(root / "07_review" / "independent-plan.json"),
                        load(root / "07_review" / "independent-records.json"), reviewers)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"passed": False, "errors": [{"code": "ADAPTER_INPUT_INVALID", "detail": str(exc)}]}
