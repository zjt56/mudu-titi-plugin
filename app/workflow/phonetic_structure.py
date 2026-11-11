"""Source-proven exceptions for scoped phonetic exercise groups, never all groups."""
from pathlib import Path
from urllib.parse import unquote, urlsplit


def verified_groups(batch_path):
    """Rebuild from current source; no persisted flag or arbitrary attachment is proof."""
    from . import question_draft
    path = Path(batch_path).resolve()
    root = path.parent.parent if path.parent.name == "06_batch" else path.parent
    try:
        draft = question_draft.build(root)
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    from .agent_tasks import confirmed_units
    from collections import Counter
    phonetic = {u["unit_id"] for u in confirmed_units(root) if u.get("type") == "语音题"}
    owners = Counter(a.get("name") for item in draft["items"] for a in item.get("attachments") or []
                     if a.get("kind") == "image")
    invalid_media = set()
    for item in draft["items"]:
        for attachment in item.get("attachments") or []:
            if attachment.get("kind") != "image":
                continue
            name = attachment.get("name") or ""
            media = root / "01_source/media" / name
            if (owners[name] != 1 or not media.is_file() or media.is_symlink() or media.stat().st_size == 0
                    or not media.resolve().is_relative_to(root / "01_source/media")):
                invalid_media.add(item["unit_id"])
    invalid = {p.get("unit_id") for p in draft["issues"]
               if p.get("code") not in {"TABLE_LAYOUT_UNVERIFIED"}}
    return {item["unit_id"]: dict(item, _source_media_batch=root.name) for item in draft["items"]
            if item.get("unit_id") in phonetic
            and item.get("unit_id") not in invalid | invalid_media
            and item.get("identity_version") == "scoped-v1"
            and item.get("unit_kind") == "group"
            and draft["source_bindings"][item["unit_id"]].get("shared_rubric")
            and str(item.get("stem") or "").strip()}


def shape(unit, verified=None):
    """Return (material optional, alias stem optional) after current-source comparison."""
    if unit.get("question_type") != "语音题" or unit.get("unit_kind") != "group":
        return False, False
    expected = (verified or {}).get(unit.get("unit_id"))
    if not expected or str(expected.get('material') or '').strip():
        return False, False
    if unit.get('materialBlocks') != expected.get('materialBlocks'):
        return False, False
    for key in ("identity_version", "section_id", "source_section", "source_span", "question_ids", "numbers", "stem"):
        if unit.get(key) != expected.get(key):
            return False, False
    subs, wanted = unit.get("subquestions") or [], expected.get("subquestions") or []
    if not subs or len(subs) != len(wanted):
        return False, False
    for child, source in zip(subs, wanted):
        if any(child.get(key) != source.get(key) for key in ("number", "question_id", "stem", "options")):
            return False, False
    images = {a["name"] for a in expected.get("attachments") or []
              if a.get("kind") == "image" and a.get("placement") == "question"}
    def source_url(attachment):
        parsed = urlsplit(str(attachment.get('url') or ''))
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return False
        name = attachment.get('name')
        return unquote(parsed.path) in {
            '01_source/media/' + str(name),
            '/source-media/' + expected['_source_media_batch'] + '/' + str(name)}
    present = {a.get("name") for a in unit.get("attachments") or []
               if isinstance(a, dict) and a.get("kind") == "image" and a.get("placement") == "question"
               and source_url(a)}
    if not images.issubset(present):
        return False, False
    alias = (bool(images) and len(subs) == 1 and expected.get("printed_numbers") == []
             and unit.get("printed_numbers") == [] and unit.get("source_span") == expected.get("source_span")
             and expected.get("source_span", {}).get("number_binding") == "unnumbered_section")
    complete = all(str(c.get("stem") or "").strip() for c in subs)
    return (bool(images) or complete), alias
