"""Maintain the real upload payload while accepted local stages fill its fields.

No manifest, approval or remote operations happen here. A caller must validate and
accept stage artifacts before sync(); the sidecar records filling, never approval.
Unmanaged batches are deliberately read-only. Conflicts raise ValueError.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from pathlib import Path

from . import question_draft
from .wire_format import FILTER_KEYS, SOURCE_KEYS

BATCH = "06_batch/batch.json"
BATCH_BASE = "06_batch/batch.base.json"
STATE = "workflow/batch-fill-state.json"
VERSION = "upload-workflow.batch-fill/v1"
ARTIFACTS = {
    "answers": "01_source_ans/answers.json", "types": "03_types/types.json",
    "explanations": "04_knowledge/explanations.json",
    "knowledge": "04_knowledge/knowledge.json", "audio": "02_audio/audio_map.json",
    "metadata": "01_source/metadata.json",
}
BODY = ("id", "unit_id", "unit_kind", "number", "numbers", "stem", "material",
        "options", "materialBlocks", "material_blocks", "attachments")
RESET = ("question_type", "type", "answer", "answers", "difficulty",
         "difficultyEvidence", "difficulty_evidence", "explanation", "articleSummary",
         "knowledge", "knowledgeIds", "knowledge_ids", "wordCount", "words",
         "contentComplete", "textbook", "audio", "audioFile")
META = (*SOURCE_KEYS, "region", "paper_title")


def _root(batch_dir):
    path = Path(batch_dir).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Batch fill root cannot contain symlinks")
    return path.resolve()


def _path(root, relative):
    if (not isinstance(relative, str) or not relative or chr(92) in relative
            or Path(relative).is_absolute() or any(p in (".", "..") for p in relative.split("/"))):
        raise ValueError("Batch fill expects safe batch-relative paths")
    return question_draft._safe(root, relative)


def _read(root, relative):
    path = _path(root, relative)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else None


def _atomic(root, relative, value):
    target = _path(root, relative)
    content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if target.is_file():
        existing = target.read_bytes()
        if existing == content or json.loads(existing.decode("utf-8-sig")) == value:
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".batch-fill-", suffix=".tmp", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
        _path(root, relative)
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True


def _uid(row):
    uid = row.get("unit_id") or row.get("id")
    if not isinstance(uid, str) or not uid or (row.get("id") and row.get("unit_id") and row["id"] != row["unit_id"]):
        raise ValueError("Missing or conflicting batch unit identity")
    return uid


def _rows(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Batch fill expects an items array")
    result = {}
    for row in payload["items"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid batch fill item")
        uid = _uid(row)
        if uid in result:
            raise ValueError("Duplicate batch fill unit: " + uid)
        _children(row)
        result[uid] = row
    return result


def _number(value):
    if isinstance(value, bool) or not str(value).isdigit():
        raise ValueError("Invalid question number")
    return str(int(value))


def _children(row):
    result = {}
    children = row.get("subquestions", [])
    if not isinstance(children, list):
        raise ValueError("Invalid subquestions")
    for child in children:
        if not isinstance(child, dict):
            raise ValueError("Invalid child item")
        number = _number(child.get("number"))
        if number in result:
            raise ValueError("Duplicate child number")
        result[number] = child
    return result


def _body(row):
    result = {k: copy.deepcopy(row[k]) for k in BODY if k in row}
    # Audio is owned by its stage; source image attachments remain protected.
    if "attachments" in result:
        result["attachments"] = [a for a in result["attachments"] if a.get("kind") != "audio"]
    if not result.get("attachments"):
        result.pop("attachments", None)
    result["subquestions"] = [_body(c) for c in row.get("subquestions", [])]
    return result


def _canonical_body(row):
    """Body identity for the fill guard.

    An attachment's URL is a transport location, not its identity: rewriting
    ``01_source/media/x.png`` to ``/source-media/<batch>/x.png`` (or the
    content-addressed deploy form) selects the same declared file. Which file is
    attached stays protected through ``name``/``kind``/placement and the bound
    media hashes; ownership and existence are still enforced at upload time.
    """
    result = _body(row)

    def clean_blocks(node):
        # A missing empty list and items=[] carry the same cell content. Limit
        # this equivalence to table cells/nested text items; keep spans, text,
        # images and nonempty lists protected by the normal body comparison.
        if not isinstance(node, dict):
            return
        if node.get("items") == []:
            node.pop("items")
        for child in node.get("items", []):
            clean_blocks(child)

    def clean(node):
        if not isinstance(node, dict):
            return node
        for key in ("materialBlocks", "material_blocks"):
            for block in node.get(key) or []:
                if not isinstance(block, dict):
                    continue
                for row in block.get("rows") or []:
                    if isinstance(row, dict):
                        for cell in row.get("cells") or []:
                            clean_blocks(cell)
        attachments = node.get("attachments")
        if isinstance(attachments, list):
            rows = []
            for item in attachments:
                if isinstance(item, dict):
                    item = dict(item)
                    name = item.get("name")
                    url = item.get("url")
                    if not name and isinstance(url, str):
                        name = url.rsplit("/", 1)[-1]
                    if name:
                        item["name"] = name
                    item.pop("url", None)
                rows.append(item)
            node["attachments"] = rows
        for child in node.get("subquestions") or []:
            clean(child)
        return node

    return clean(result)


def _base(row, meta=None):
    row = copy.deepcopy(row)
    meta = meta or {}
    # New objects contain upload/wire fields only. Local judgments remain in
    # their artifacts/state; region is a filter and paper_title is batch-level.
    for node in (row, *row.get("subquestions", [])):
        node["explanation"] = node["articleSummary"] = ""
        filters = node.setdefault("filterMetadata", {})
        region = node.pop("region", filters.get("region", meta.get("region", "")))
        node.pop("paper_title", None)
        node.pop("contentComplete", None)
        filters.pop("paper_title", None)
        filters.pop("contentComplete", None)
        filters.setdefault("subtypes", [])
        filters["region"] = region
        for key in SOURCE_KEYS:
            node.setdefault(key, copy.deepcopy(meta.get(key, "")))
        for key in SOURCE_KEYS:
            if node.get(key) not in (None, "", []):
                filters[key] = copy.deepcopy(node[key])
    # A subquestion inherits its group's main type; writing an explicit empty
    # string there is read as "main type is empty", not "inherit", so it would
    # invalidate every grouped question's labels. Difficulty likewise stays
    # absent until a stage judges it: an explicit "" conflicts with the filter
    # value written by the metadata step and fails the upload alias check.
    row.setdefault("question_type", copy.deepcopy(meta.get("question_type", "")))
    return row


def _source_meta(root):
    confirmed = _read(root, "01_source/structure.confirmed.json") or {}
    meta = confirmed.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("Invalid source metadata")
    return meta


def _set_batch_title(payload, artifact):
    candidates = []
    for node in (artifact, artifact.get("meta", {}), *artifact.get("items", [])):
        if not isinstance(node, dict):
            continue
        title = node.get("paper_title")
        if isinstance(title, str) and title.strip():
            candidates.append(title)
    if len(set(candidates)) > 1:
        raise ValueError("Conflicting paper titles in metadata payload")
    if candidates:
        payload["name"] = candidates[0]


def _validate_binding(root, binding):
    if (not isinstance(binding, dict) or binding.get("source") != "01_source/正文.txt"
            or type(binding.get("start_line")) is not int or type(binding.get("end_line")) is not int
            or not 1 <= binding["start_line"] <= binding["end_line"]):
        raise ValueError("Cannot fill batch without proven source binding")
    digest = binding.get("source_text_sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or digest == hashlib.sha256(b"").hexdigest()):
        raise ValueError("Invalid source binding digest")
    media = binding.get("media_hashes", {})
    if not isinstance(media, dict):
        raise ValueError("Invalid source media binding")
    for relative in media:
        _path(root, relative)


def _source_token(row, binding):
    return question_draft._digest({"body": _body(row),
        "source": binding.get("source_text_sha256"), "media": binding.get("media_hashes", {})})


def _seal(state):
    value = copy.deepcopy(state)
    value.pop("state_sha256", None)
    state["state_sha256"] = question_draft._digest(value)
    return state


def _managed(root, *, check_body=True):
    state, payload = _read(root, STATE), _read(root, BATCH)
    if state is None:
        if _path(root, STATE).exists():
            raise ValueError("Invalid null batch fill state")
        return None, payload
    if not isinstance(state, dict):
        raise ValueError("Invalid batch fill state")
    check = copy.deepcopy(state)
    digest = check.pop("state_sha256", None)
    if state.get("version") != VERSION or digest != question_draft._digest(check):
        raise ValueError("Batch fill state integrity conflict")
    if state.get("mode") not in ("filling", "finalized") or not isinstance(state.get("units"), dict):
        raise ValueError("Invalid batch fill state mode/units")
    base_payload = _read(root, BATCH_BASE)
    if not isinstance(base_payload, dict) or state.get("base_sha256") != question_draft._digest(base_payload):
        raise ValueError("Batch fill base file integrity conflict")
    bases = _rows(base_payload)
    rows = _rows(payload)
    if set(bases) != set(state["units"]):
        raise ValueError("Batch fill base identities conflict")
    if set(rows) != set(state["units"]):
        raise ValueError("Batch fill state and object identities conflict")
    for uid, row in rows.items():
        saved = state["units"][uid]
        if not isinstance(saved, dict) or not isinstance(saved.get("base"), dict):
            raise ValueError("Invalid batch fill source base")
        base = saved["base"]
        _validate_binding(root, saved.get("binding"))
        if (bases[uid] != base or _uid(base) != uid or saved.get("object_sha256") != question_draft._digest(base)
                or saved.get("source_sha256") != _source_token(base, saved.get("binding", {}))):
            raise ValueError("Batch fill source base integrity conflict")
        expected_body = (state.get("finalized_bodies", {}).get(uid) if state["mode"] == "finalized"
                         else state.get("managed_bodies", {}).get(uid, _body(base)))
        if check_body and _canonical_body(row) != _canonical_body(expected_body):
            raise ValueError("Batch fill正文 conflict for " + uid + "; existing content preserved")
    return state, payload


def _snapshot(root):
    return {relative: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            for relative in (BATCH, BATCH_BASE, STATE)
            for path in (_path(root, relative),)}


def _assert_snapshot(root, expected):
    if _snapshot(root) != expected:
        raise ValueError("Batch fill files changed during operation; existing content preserved")


def _save(root, payload, state, expected):
    # A crash between the atomic replacements fails closed on the next read.
    _assert_snapshot(root, expected)
    base = {"items": [copy.deepcopy(state["units"][_uid(row)]["base"]) for row in payload["items"]]}
    if state.get("base_name"):
        base["name"] = state["base_name"]
    state["base_sha256"] = question_draft._digest(base)
    state["payload_sha256"] = question_draft._digest(payload)
    changed = _atomic(root, BATCH, payload)
    base_changed = _atomic(root, BATCH_BASE, base)
    state_changed = _atomic(root, STATE, _seal(state))
    return changed or base_changed or state_changed


def _result(status, payload, **extra):
    return dict(status=status, payload=payload, **extra)


def _sync_media_attachments(kept, fresh, batch_name=None):
    """沿用未变单元的旧对象时，补齐 draft 权威对象里存在、旧对象缺失的图片/音频附件。

    旧 batch.json 可能因历史清洗丢过附件（如组级共享材料图），而 draft（_source_base）的
    媒体绑定才是权威。这里按 kind+name 并集补入：以 fresh 的附件为准补缺，不动题干/标签。
    draft 的附件 url 可能是本地相对路径（01_source/media/x、02_audio/x），需规范成与
    其它附件一致的已部署形式 /source-media/<quote(批次目录名)>/<name>，否则 media_inventory
    会以"Attachment URL is not owned by this batch"拒绝上传。
    """
    from urllib.parse import quote
    base = quote(batch_name, safe='') if batch_name else None

    def _norm_url(a):
        url = a.get("url")
        name = a.get("name") or (str(url).rsplit("/", 1)[-1] if url else "")
        if base and (not isinstance(url, str) or not url.startswith("/source-media/")):
            a = dict(a)
            a["url"] = "/source-media/" + base + "/" + quote(name, safe='')
            a["name"] = name
        return a

    def one(dst, src):
        if not isinstance(dst, dict) or not isinstance(src, dict):
            return
        # 先把旧对象里已有的本地相对 url 附件规范成 /source-media/...（历史遗留）。
        if base and isinstance(dst.get("attachments"), list):
            dst["attachments"] = [_norm_url(a) if isinstance(a, dict) else a
                                  for a in dst["attachments"]]
        have = {(a.get("kind"), a.get("name") or str(a.get("url", "")).rsplit("/", 1)[-1])
                for a in (dst.get("attachments") or []) if isinstance(a, dict)}
        for a in (src.get("attachments") or []):
            if not isinstance(a, dict) or a.get("kind") not in ("image", "audio"):
                continue
            key = (a.get("kind"), a.get("name") or str(a.get("url", "")).rsplit("/", 1)[-1])
            if key not in have:
                dst.setdefault("attachments", []).append(_norm_url(a))
                have.add(key)
        sdst = {c.get("number"): c for c in (dst.get("subquestions") or []) if isinstance(c, dict)}
        for c in (src.get("subquestions") or []):
            if isinstance(c, dict) and c.get("number") in sdst:
                one(sdst[c["number"]], c)
    one(kept, fresh)
    return kept


def _refresh_row(current, fresh, *, preserve_fill=False):
    row = copy.deepcopy(current)
    old_children = _children(row)
    for key in (BODY if preserve_fill else (*BODY, *RESET)):
        row.pop(key, None)
        if key in fresh:
            row[key] = copy.deepcopy(fresh[key])
    if not preserve_fill:
        filters = row.setdefault("filterMetadata", {})
        for key in ("subtypes", "difficulty", "difficultyValue"):
            filters.pop(key, None)
        filters["subtypes"] = []
    row["subquestions"] = [_refresh_row(old_children.get(_number(c["number"]), {}), c,
                                       preserve_fill=preserve_fill)
                              for c in fresh.get("subquestions", [])]
    return row


def initialize(batch_dir) -> dict:
    """Create/refresh source-bound batch objects; existing unmanaged files stay untouched."""
    root = _root(batch_dir)
    expected = _snapshot(root)
    state, payload = _managed(root)
    if state is None and _path(root, BATCH).exists():
        # The preassemble delivery is a fresh source-only artifact. Writing it
        # does not adopt, validate, or alter the existing unmanaged batch.
        draft = question_draft.ensure(root)
        rows = _rows(draft)
        bindings = draft.get("source_bindings") or {}
        if not rows or not isinstance(bindings, dict):
            raise ValueError("Cannot build source base without proven items")
        for uid in rows:
            _validate_binding(root, bindings.get(uid))
        meta = _source_meta(root)
        base = {"items": [_base(row, meta) for row in rows.values()]}
        _set_batch_title(base, meta)
        _assert_snapshot(root, expected)
        changed = _atomic(root, BATCH_BASE, base)
        return _result("untouched", payload, base_changed=changed,
                       reason="existing batch has no filling ownership state; source base only")
    draft = question_draft.ensure(root)
    rows = _rows(draft)
    bindings = draft.get("source_bindings") or {}
    if not rows or not isinstance(bindings, dict):
        raise ValueError("Cannot fill batch without proven source-bound items")
    for uid in rows:
        _validate_binding(root, bindings.get(uid))
    if state is not None and state["mode"] == "finalized":
        unchanged = set(rows) == set(state["units"]) and all(
            _source_token(_base(row), bindings[uid]) == state["units"][uid]["source_sha256"]
            for uid, row in rows.items())
        if unchanged:
            # 即便整体已完成且源未变，也要把 draft 权威媒体附件补齐到旧对象：历史清洗
            # 可能丢过图片附件（如组级共享材料图），否则上传会报 SOURCE_IMAGE_MISSING。
            media_changed = False
            by_uid = {it.get("unit_id") or it.get("id"): it for it in (payload.get("items") or [])}
            for uid, row in rows.items():
                fresh = _base(row, _source_meta(root))
                target = by_uid.get(uid)
                if target is not None:
                    before = copy.deepcopy(target.get("attachments"))
                    _sync_media_attachments(target, fresh, root.name)
                    if target.get("attachments") != before:
                        media_changed = True
            if media_changed:
                # 附件补齐改变了对象正文，必须同步 finalized_bodies，否则 _managed 会
                # 判定正文冲突；只更新实际变化的单元。
                fbs = state.setdefault("finalized_bodies", {})
                for uid, row in by_uid.items():
                    fbs[uid] = _body(row)
                _save(root, payload, state, expected)
                return _result("refreshed", payload)
            return _result("finalized", payload)
        state["mode"] = "filling"
        state["managed_bodies"] = copy.deepcopy(state.get("finalized_bodies", {}))
    previous = _rows(payload) if payload is not None else {}
    created = state is None
    if created:
        if _path(root, BATCH_BASE).exists():
            raise ValueError("Unmanaged batch base file already exists")
        state = {"version": VERSION, "mode": "filling", "units": {}}
        payload = {"items": []}
    meta = _source_meta(root)
    source_header = {}
    _set_batch_title(source_header, meta)
    if source_header.get("name"):
        state["base_name"] = source_header["name"]
        if created or not payload.get("name"):
            payload["name"] = source_header["name"]
    changed_units, units, output = [], {}, []
    for uid, source in rows.items():
        fresh = _base(source, meta)
        binding = copy.deepcopy(bindings[uid])
        token = _source_token(fresh, binding)
        old = state["units"].get(uid)
        if old is not None and old["source_sha256"] == token:
            kept = copy.deepcopy(previous[uid])
            before = _body(kept)
            _sync_media_attachments(kept, fresh, root.name)
            installed = _body(kept)
            if installed != before:
                # 附件补齐/URL 规范化改写了本对象正文，必须同步托管正文，
                # 否则下一次 _managed 会把自己刚写入的部署路径误判成人工篡改。
                state.setdefault("managed_bodies", {})[uid] = copy.deepcopy(installed)
            output.append(kept)
            # Line shifts or parser diagnostics do not invalidate labels.
            units[uid] = dict(old, binding=binding)
        else:
            old_binding = old.get("binding", {}) if old is not None else {}
            old_base = old.get("base", {}) if old is not None else {}
            # A parser-only body repair must not discard labels already filled
            # against the same Word fragment, media and question identities.
            preserve_fill = (uid in previous and bool(old_binding.get("source_text_sha256"))
                             and old_binding.get("source_text_sha256") == binding.get("source_text_sha256")
                             and old_binding.get("media_hashes", {}) == binding.get("media_hashes", {})
                             and all(old_base.get(key) == fresh.get(key)
                                     for key in ("id", "unit_id", "unit_kind", "number", "numbers"))
                             and set(_children(old_base)) == set(_children(fresh)))
            output.append(_refresh_row(previous[uid], fresh, preserve_fill=preserve_fill)
                          if uid in previous else fresh)
            units[uid] = {"base": fresh, "object_sha256": question_draft._digest(fresh),
                          "source_sha256": token, "binding": binding,
                          "stages": copy.deepcopy(old.get("stages", {})) if preserve_fill else {}}
            state.get("managed_bodies", {}).pop(uid, None)
            changed_units.append(uid)
    if "managed_bodies" in state:
        state["managed_bodies"] = {uid: body for uid, body in state["managed_bodies"].items() if uid in units}
    payload["items"] = output
    removed = sorted(set(previous) - set(rows))
    state["units"] = units
    state["draft_input_hashes"] = copy.deepcopy(draft.get("input_hashes", {}))
    state["draft_issues"] = copy.deepcopy(draft.get("issues", []))
    changed = _save(root, payload, state, expected)
    return _result("initialized" if created else ("refreshed" if changed else "unchanged"),
                   payload, changed_units=changed_units, removed_units=removed)


def _overlay(target, source, stage):
    # Runtime-only import: assemble_local may call this module without a cycle.
    from .assemble_local import _overlay_labels, _overlay_text
    if stage == "types":
        if "type" in source:
            target["question_type"] = copy.deepcopy(source["type"])
        for key, output in (("words", "wordCount"),):
            if key in source:
                target[output] = copy.deepcopy(source[key])
        if "subtypes" in source:
            target.setdefault("filterMetadata", {})["subtypes"] = copy.deepcopy(source["subtypes"])
    elif stage == "knowledge":
        _overlay_labels(target, source)
        if "textbook" in source:
            target["textbook"] = copy.deepcopy(source["textbook"])
    elif stage == "explanations":
        for key in ("explanation", "articleSummary"):
            _overlay_text(target, source, {}, key)
    elif stage == "answers":
        for key in ("answer", "difficulty"):
            if key in source:
                target[key] = copy.deepcopy(source[key])
    elif stage == "metadata":
        for key in SOURCE_KEYS:
            if key in source and source[key] not in (None, "", []):
                value = copy.deepcopy(source[key])
                if key == "year" and str(value).isdigit():
                    value = int(value)
                target[key] = value
                target.setdefault("filterMetadata", {})[key] = value
        for key in FILTER_KEYS:
            if key in source and source[key] not in (None, "", []):
                target.setdefault("filterMetadata", {})[key] = copy.deepcopy(source[key])


def _merge_metadata_dict(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_metadata_dict(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _overlay_item_metadata(target, source):
    # Metadata artifacts may contain a full batch snapshot. Its content is a
    # comparison baseline only; this stage cannot change wording or structure.
    for key in BODY:
        if key in source and source[key] != target.get(key):
            raise ValueError("Metadata payload changes protected body field: " + key)
    filters = source.get("filterMetadata", {})
    if not isinstance(filters, dict):
        raise ValueError("Metadata filterMetadata must be an object")
    existing = target.setdefault("filterMetadata", {})
    if not isinstance(existing, dict):
        raise ValueError("Existing filterMetadata conflicts with metadata object")
    allowed_filters = FILTER_KEYS | set(SOURCE_KEYS) | {"difficulty"}
    # Preserve existing extensions, but never introduce unknown upload fields.
    accepted_filters = {key: value for key, value in filters.items() if key in allowed_filters}
    _merge_metadata_dict(existing, accepted_filters)
    _overlay(target, source, "metadata")
    for key in ("difficulty", "wordCount"):
        if key in source:
            target[key] = copy.deepcopy(source[key])
    for key in (*SOURCE_KEYS, "difficulty"):
        top = source.get(key)
        nested = accepted_filters.get(key)
        value = top if top not in (None, "", []) else nested
        if value not in (None, "", []):
            value = int(value) if key == "year" and str(value).isdigit() else copy.deepcopy(value)
            target[key] = value
            existing[key] = copy.deepcopy(value)


def _apply(root, data, stage, artifact):
    if not isinstance(artifact, dict):
        raise ValueError("Stage payload must be an accepted artifact object")
    rows, touched, ignored = _rows(data), set(), []
    if stage == "metadata":
        _set_batch_title(data, artifact)
    if stage == "metadata" and "items" not in artifact:
        meta = artifact.get("meta", artifact)
        if not isinstance(meta, dict):
            raise ValueError("Invalid metadata artifact")
        for uid, row in rows.items():
            for node in (row, *row.get("subquestions", [])):
                _overlay(node, meta, stage)
            touched.add(uid)
        return touched, ignored
    raw = artifact.get("items", [])
    if not isinstance(raw, list):
        raise ValueError("Stage items must be an array")
    seen = set()
    for source in raw:
        if not isinstance(source, dict):
            raise ValueError("Invalid stage row")
        uid = _uid(source)
        if uid not in rows or uid in seen:
            raise ValueError("Unknown or duplicate stage unit: " + uid)
        seen.add(uid)
        target = rows[uid]
        if "number" in source and _number(source["number"]) != _number(target["number"]):
            raise ValueError("Stage number does not belong to unit " + uid)
        if stage == "answers" and target.get("subquestions") and ("answer" in source or "difficulty" in source):
            raise ValueError("Group answers/difficulty must identify their child numbers")
        if stage == "metadata":
            _overlay_item_metadata(target, source)
        else:
            _overlay(target, source, stage)
        children = _children(target)
        for number, child in _children(source).items():
            if number not in children:
                raise ValueError("Stage child does not belong to unit " + uid)
            if stage == "metadata":
                _overlay_item_metadata(children[number], child)
            else:
                _overlay(children[number], child, stage)
        if stage == "audio":
            relative = source.get("file")
            if not isinstance(relative, str) or not _path(root, relative).is_file():
                raise ValueError("Audio file must be a safe existing batch-local file")
            attachments = [a for a in target.get("attachments", []) if a.get("kind") != "audio"]
            attachments.append({"kind": "audio", "url": relative, "name": Path(relative).name})
            target["attachments"] = attachments
        touched.add(uid)
    if stage == "answers":
        from .agent_tasks import confirmed_units
        from .question_identity import question_key
        units = {u["unit_id"]: u for u in confirmed_units(root)}
        owners = {}
        for uid, row in rows.items():
            targets = list(_children(row).values()) or [row]
            for node in targets:
                owners.setdefault(question_key(units.get(uid, row), node["number"]), []).append((uid, node))
        for key, output in (("answers", "answer"), ("difficulty", "difficulty")):
            values = artifact.get(key, {})
            if not isinstance(values, dict):
                raise ValueError("Invalid numbered answer/difficulty mapping")
            normalized = set()
            for number, value in values.items():
                if not isinstance(number, str) or not number:
                    raise ValueError("Answer keys must be confirmed string question identities")
                # Legacy numeric keys retain their strict normalization checks.
                if number.isdigit():
                    number = _number(number)
                if number in normalized or len(owners.get(number, [])) > 1:
                    raise ValueError("Numbered stage answer has no unique uid ownership: " + number)
                normalized.add(number)
                if number not in owners:
                    ignored.append({"field": key, "number": number, "reason": "no confirmed unit owns this source number"})
                    continue
                uid, node = owners[number][0]
                node[output] = copy.deepcopy(value)
                touched.add(uid)
    return touched, ignored


def sync(batch_dir, stage, payload=None) -> dict:
    """Merge only this accepted stage's fields; no import/approval is performed.

    answers accepts global number maps only with unique batch-wide ownership, or
    items:[{unit_id, answer, difficulty, subquestions:[{number, answer,...}]}].
    Other per-unit stages use their existing items artifact shapes. Metadata
    accepts paper fields/meta, or items with per-unit/child metadata patches. A
    complete batch snapshot is allowed only when included body fields match;
    answers, explanations, knowledge and type fields in it are never copied.
    """
    if stage not in ARTIFACTS:
        raise ValueError("Unsupported batch fill stage: " + str(stage))
    initialized = initialize(batch_dir)
    if initialized["status"] == "untouched":
        return initialized
    root = _root(batch_dir)
    expected = _snapshot(root)
    state, data = _managed(root)
    if state["mode"] == "finalized":
        state["mode"] = "filling"
        state["managed_bodies"] = copy.deepcopy(state["finalized_bodies"])
    artifact = _read(root, ARTIFACTS[stage]) if payload is None else copy.deepcopy(payload)
    if stage == "metadata" and payload is None:
        confirmed = _read(root, "01_source/structure.confirmed.json") or {}
        fallback = confirmed.get("meta") or {}
        if not isinstance(fallback, dict):
            raise ValueError("Invalid confirmed paper metadata")
        identified = artifact.get("meta", artifact) if isinstance(artifact, dict) else {}
        artifact = dict(fallback, **{k: v for k, v in identified.items() if v not in (None, "", [])})
    if artifact is None:
        raise ValueError("Missing stage artifact: " + ARTIFACTS[stage])
    touched, ignored = _apply(root, data, stage, artifact)
    for uid in touched:
        entry = {"artifact_sha256": question_draft._digest(artifact)}
        if stage == "types":
            source = next((row for row in artifact.get("items", []) if _uid(row) == uid), {})
            if "contentComplete" in source:
                entry["contentComplete"] = copy.deepcopy(source["contentComplete"])
        state["units"][uid]["stages"][stage] = entry
    changed = _save(root, data, state, expected)
    return _result("filled" if changed else "unchanged", data, stage=stage, units=sorted(touched),
                   ignored=ignored, notes=["Ignored source numbers without confirmed unit ownership"] if ignored else [])


def project_audio_views(batch_dir, before):
    """Project accepted media only; retain every existing non-audio view field."""
    root = _root(batch_dir)
    after = _read(root, BATCH)
    def without_audio(value):
        if isinstance(value, dict):
            return {key: ([without_audio(a) for a in child if a.get('kind') != 'audio']
                          if key == 'attachments' and isinstance(child, list) else without_audio(child))
                    for key, child in value.items() if key not in ('audio', 'audioFile')
                    and not (key == 'attachments' and isinstance(child, list)
                             and not any(a.get('kind') != 'audio' for a in child))}
        if isinstance(value, list):
            return [without_audio(child) for child in value]
        return value
    if without_audio(before) != without_audio(after):
        left, right = without_audio(before), without_audio(after)
        changed = [key for key in set(left) | set(right) if left.get(key) != right.get(key)]
        if changed == ['items']:
            changed = sorted({key for a, b in zip(left['items'], right['items'])
                              for key in set(a) | set(b) if a.get(key) != b.get(key)})
        raise ValueError('Audio delivery changed non-audio batch fields: ' + ', '.join(changed))
    source = _rows(after)
    candidates = []
    for relative in ('06_batch/batch_meta.base.json', '06_batch/batch_meta.json'):
        view = _read(root, relative)
        if view is None:
            continue
        original = copy.deepcopy(view)
        rows = _rows(view)
        if set(rows) != set(source):
            raise ValueError('Audio projection requires exact derived unit coverage')
        for uid, row in rows.items():
            for key in ('number', 'numbers', 'unit_kind'):
                if row.get(key) != source[uid].get(key):
                    raise ValueError('Audio projection unit identity differs: ' + uid)
            audio = [copy.deepcopy(a) for a in source[uid].get('attachments', []) if a.get('kind') == 'audio']
            row['attachments'] = [a for a in row.get('attachments', []) if a.get('kind') != 'audio'] + audio
        if without_audio(view) != without_audio(original):
            raise ValueError('Audio projection changed non-audio view fields')
        candidates.append((relative, view))
    for relative, view in candidates:
        _atomic(root, relative, view)
    return after


def is_filling(batch_dir) -> bool:
    """Read-only ownership check. Conflicts are errors, never an implicit fallback."""
    state, _ = _managed(_root(batch_dir))
    return state is not None and state["mode"] == "filling"


def mark_finalized(batch_dir) -> dict:
    """End filling after the caller's formal assemble validation/acceptance.

    This marks no manifest or review complete and provides no validation itself.
    """
    root = _root(batch_dir)
    expected = _snapshot(root)
    state, payload = _managed(root, check_body=False)
    if state is None:
        return _result("untouched", payload)
    # The caller has validated the installed normalization; compare its source
    # ownership rather than rejecting legitimate final field normalization.
    draft = question_draft.ensure(root)
    rows = _rows(draft)
    if set(rows) != set(state["units"]):
        raise ValueError("Source units changed before finalization")
    for uid, row in rows.items():
        token = _source_token(_base(row), draft.get("source_bindings", {}).get(uid, {}))
        if token != state["units"][uid]["source_sha256"]:
            saved = state["units"][uid]
            before, after = _body(saved["base"]), _body(_base(row))
            changed = sorted(key for key in set(before) | set(after)
                             if before.get(key) != after.get(key))
            binding = draft.get("source_bindings", {}).get(uid, {})
            old_binding = saved.get("binding", {})
            if old_binding.get("source_text_sha256") != binding.get("source_text_sha256"):
                changed.append("source_text_sha256")
            if old_binding.get("media_hashes", {}) != binding.get("media_hashes", {}):
                changed.append("media_hashes")
            raise ValueError("Source changed before finalization: " + uid
                             + "; changed fields: " + ", ".join(changed or ["source binding"]))
    state["mode"] = "finalized"
    state["finalized_bodies"] = {uid: _body(row) for uid, row in _rows(payload).items()}
    state["payload_sha256"] = question_draft._digest(payload)
    _assert_snapshot(root, expected)
    _atomic(root, STATE, _seal(state))
    return _result("finalized", payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="initialize", choices=("initialize", "sync", "finalize"))
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--stage", choices=tuple(ARTIFACTS))
    args = parser.parse_args(argv)
    try:
        if args.action == "sync":
            if not args.stage:
                parser.error("sync requires --stage")
            result = sync(args.batch_dir, args.stage)
        elif args.action == "finalize":
            result = mark_finalized(args.batch_dir)
        else:
            result = initialize(args.batch_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "conflict", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({k: v for k, v in result.items() if k != "payload"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
