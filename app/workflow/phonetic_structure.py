"""Current-source proof for phonetic and legitimate unnumbered native groups."""
from pathlib import Path
import re
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
    from .question_content import legitimate_source_shape
    eligible = {u['unit_id']: u.get('type') for u in confirmed_units(root)
                if u.get('type') == '语音题' or (
                    u.get('unit_kind') == 'group' and u.get('numbers') == [1]
                    and (u.get('source_span') or {}).get('number_binding') == 'unnumbered_section'
                    and legitimate_source_shape(u) in {'image_only', 'unnumbered_no_image'})}
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
    return {item["unit_id"]: dict(item, _source_media_batch=root.name,
                                   _source_question_type=eligible[item['unit_id']]) for item in draft["items"]
            if item.get("unit_id") in eligible
            and item.get("unit_id") not in invalid | invalid_media
            and item.get("identity_version") == "scoped-v1"
            and item.get("unit_kind") == "group"
            and draft["source_bindings"][item["unit_id"]].get("shared_rubric")
            and str(item.get("stem") or "").strip()}


def shape(unit, verified=None):
    """Return (material optional, alias stem optional) after current-source comparison."""
    if unit.get('unit_kind') != 'group':
        return False, False
    expected = (verified or {}).get(unit.get('unit_id'))
    if not expected or unit.get('question_type') != expected.get('_source_question_type'):
        return False, False
    if unit.get('material') != expected.get('material'):
        return False, False
    if unit.get('materialBlocks') != expected.get('materialBlocks'):
        return False, False
    for key in ("identity_version", "section_id", "source_section", "source_span", "question_ids", "numbers", "stem"):
        if unit.get(key) != expected.get(key):
            return False, False
    subs, wanted = unit.get("subquestions") or [], expected.get("subquestions") or []
    if not subs or len(subs) != len(wanted):
        return False, False
    def option_pairs(options):
        if not isinstance(options, list):
            return None
        pairs = []
        for option in options:
            if isinstance(option, dict):
                label, text = option.get('label', option.get('key')), option.get('text')
            elif isinstance(option, str):
                match = re.fullmatch(r'\s*([A-H])[.．、]\s*(.*)', option, re.S)
                if not match:
                    return None
                label, text = match.groups()
            else:
                return None
            if label not in list('ABCDEFGH') or not isinstance(text, str):
                return None
            pairs.append((label, text.strip()))
        return pairs
    for child, source in zip(subs, wanted):
        if any(child.get(key) != source.get(key) for key in ("number", "question_id", "stem")):
            return False, False
        actual_options, source_options = option_pairs(child.get('options')), option_pairs(source.get('options'))
        if actual_options is None or actual_options != source_options:
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
    alias = (len(subs) == 1 and expected.get('printed_numbers') == []
             and unit.get('printed_numbers') == [] and unit.get('source_span') == expected.get('source_span')
             and expected.get('source_span', {}).get('number_binding') == 'unnumbered_section'
             and (bool(images) or bool(str(expected.get('material') or '').strip())))
    def source_choices_only(child):
        # This exception is source-proven, not an invitation to erase stems:
        # every child must originally contain only the full printed A-D choices.
        if str(child.get('stem') or '').strip():
            return False
        options = child.get('options')
        if not isinstance(options, list) or len(options) != 4:
            return False
        labels = []
        for option in options:
            if isinstance(option, dict):
                label, text = option.get('label', option.get('key')), option.get('text')
            elif isinstance(option, str):
                match = re.match(r'^\s*([A-D])[.．、]\s*(.*)$', option, re.S)
                if not match:
                    return False
                label, text = match.groups()
            else:
                return False
            if not isinstance(text, str) or not text.strip():
                return False
            labels.append(label)
        return labels == list('ABCD')
    choices_only = (expected.get('_source_question_type') == '语音题'
                    and not str(expected.get('material') or '').strip()
                    and all(source_choices_only(child) for child in wanted))
    complete = all(str(c.get('stem') or '').strip() for c in subs)
    return (bool(images) or complete or alias or choices_only), (alias or choices_only)
