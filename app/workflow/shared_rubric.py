"""Source-backed instructions shared by questions in a section."""
import re

GROUP_MATERIAL_RULE = (
    "【题组共享材料】按当前原卷的完整题组窗口，核对共享文字、表格、图片或音频的实际归属，"
    "并核对组级作答导语与全部成员小题。仅当原卷和题型契约证明本组无需共享文字，"
    "且当前阶段要求的其余内容均已交付时，才允许 material 为空。"
    "同组已绑定的原生图片附件或结构化表格可承载共享材料，子题图、装饰图或邻组附件不可替代。"
    "阶段尚未交付的音频按听力契约处理，不据此报文字缺失。"
    "真实缺失或错绑须指出单元 ID、原文位置、具体缺项及首个有证据的丢失或覆盖环节。"
    "仅按唯一来源恢复受影响单元，同证据不原样重试，不造占位、不跨题借用，也不按批次或题号特判。"
)

SECTION_LEAD = re.compile(r"^\s*第[一二三四五六七八九十百零]+\s*(?:部分|节)\s*")
_TYPES = {"完形填空", "语法选择", "阅读理解", "任务型阅读"}


def instruction(line, kind=""):
    from .extract import _plain, guide_numbers, material_label
    from .preview import _strong_number
    text = SECTION_LEAD.sub("", _plain(line).strip(), count=1)
    if (kind and kind not in _TYPES) or _strong_number(line) is not None or material_label(line):
        return False
    # Numbered lead-ins belong exclusively to their explicitly named group.
    if guide_numbers(line) or re.search(r"第\s*\d+|\d+\s*(?:至|到|[-—~～])\s*\d+\s*题", text):
        return False
    return bool(re.match(r"阅读(?:下列|下面)短文", text)
                or re.match(r"(?:将答案填写|答案写在|从每小题所给)", text))


def picture_sentence_group(unit):
    """Identify the existing lesson rule for completing sentences from a picture."""
    from .sync_practice import heading_hit
    hit = heading_hit((unit.get("source_section") or {}).get("title") or "") or {}
    return (unit.get("unit_kind") == "group" and unit.get("type") == "完成句子"
            and hit.get("rule") == "sentence-picture")


def picture_spelling_group(unit):
    """Identify the confirmed synchronous-practice picture word-spelling group rule.

    Example: ``**Ⅱ. 根据家谱/地图/图片信息完成单词**`` (`spell-picture`).
    When a shared image sits between the heading and item 1 and is promoted to a
    native ``placement=question`` attachment, the group's text ``material`` is
    legitimately empty while the heading carries the rubric and the attachment
    carries the shared picture. Unnumbered pure-image sections remain owned by
    the ``native_alias`` path so child alias semantics stay intact.
    """
    from .sync_practice import heading_hit
    if (unit.get("source_span") or {}).get("number_binding") in {
            "unnumbered_section", "answer_inferred_image_group"}:
        return False
    hit = heading_hit((unit.get("source_section") or {}).get("title") or "") or {}
    qtype = unit.get("type") or unit.get("_source_question_type")
    return (unit.get("unit_kind") == "group" and qtype == "单词拼写"
            and hit.get("rule") == "spell-picture" and hit.get("kind") == "group")


def heading_word_bank_group(unit):
    """Identify a word-bank group whose candidate words are printed in the section heading.

    Example: ``**Ⅱ. 用can、can’t、could或couldn’t的适当形式填空**`` (`spell-modal`).
    There is no separate word box between the heading and item 1: the section
    heading itself carries the complete shared prompt and candidate words.
    """
    from .sync_practice import heading_hit
    hit = heading_hit((unit.get("source_section") or {}).get("title") or "") or {}
    return (unit.get("unit_kind") == "group" and unit.get("type") == "选词填空"
            and hit.get("rule") == "spell-modal")


def phonetic_write_group(unit):
    """Recognize only the confirmed synchronous-practice phonetic spelling group rule."""
    from .sync_practice import heading_hit
    hit = heading_hit((unit.get("source_section") or {}).get("title") or "") or {}
    qtype = unit.get("type") or unit.get("_source_question_type")
    return (unit.get("unit_kind") == "group" and qtype == "单词拼写"
            and hit.get("rule") == "phonetic-write" and hit.get("kind") == "group")


def dialogue_word_group(unit):
    """Identify the synchronous-practice fill-in-word mini-dialogue group (`dialogue-word`).

    Example: ``**Ⅱ. 填入恰当的特殊疑问词或词组,完成对话**``.
    Each subquestion carries its own standalone mini-dialogue and blank, so there
    is no shared passage between the heading and item 1; the printed section
    heading itself is the source-backed group instruction.
    """
    from .sync_practice import heading_hit
    hit = heading_hit((unit.get("source_section") or {}).get("title") or "") or {}
    qtype = unit.get("type") or unit.get("_source_question_type")
    return (unit.get("unit_kind") == "group" and qtype == "补全对话"
            and hit.get("rule") == "dialogue-word" and hit.get("kind") == "group")


def native_section_rubric(lines, unit, *, band_evidence=None):
    """Bind the printed heading of an intact, scoped source group."""
    from .question_content import legitimate_source_shape
    span_binding = (unit.get('source_span') or {}).get('number_binding')
    native_alias = (unit.get('unit_kind') == 'group'
                    and (
                        (unit.get('numbers') == [1] and span_binding == 'unnumbered_section'
                         and legitimate_source_shape(unit) in {'image_only', 'unnumbered_no_image'})
                        or (span_binding == 'answer_inferred_image_group'
                            and bool(unit.get('numbers'))
                            and legitimate_source_shape(unit) == 'image_only')
                    ))
    picture = picture_sentence_group(unit) or picture_spelling_group(unit)
    word_bank_heading = heading_word_bank_group(unit)
    phonetic_write = phonetic_write_group(unit)
    dialogue_word = dialogue_word_group(unit)
    if phonetic_write:
        from .sync_practice import looks_like_sync_practice
        phonetic_write = looks_like_sync_practice("\n".join(lines))
    if (unit.get("identity_version") != "scoped-v1"
            or unit.get('type') != '语音题' and not native_alias and not picture
            and not word_bank_heading and not phonetic_write and not dialogue_word):
        return []
    section, span = unit.get("source_section") or {}, unit.get("source_span") or {}
    heading, first, last = section.get("start_line"), span.get("start_line"), span.get("end_line")
    if (span.get("basis") not in {"cleaned_source", "ai_classified"}
            or not all(type(n) is int for n in (heading, first, last, section.get("end_line")))
            or not 1 <= heading < first <= last <= section["end_line"] <= len(lines)):
        return []
    from .lesson_bands import annotate_sections, strip_decorative_images

    title = strip_decorative_images(section.get("title") or "")
    heading_text = strip_decorative_images(lines[heading - 1])
    if not title or heading_text != title:
        return []
    # Reconstruct the owner independently: persisted section end/span cannot
    # extend this instruction into the next exercise or answer appendix.
    from .sync_practice import heading_candidates
    owners = [row for row in annotate_sections(heading_candidates("\n".join(lines)), band_evidence or {})
              if row["start_line"] == heading]
    if (len(owners) != 1 or owners[0]['type'] != unit.get('type')
            or owners[0]['kind'] != 'group'
            or strip_decorative_images(owners[0]["title"]) != title
            or owners[0]["end_line"] != section["end_line"]
            or last > owners[0]["end_line"]):
        return []
    # Prove whole-section ownership. Empty margins are harmless; omitted
    # content and repeated or missing printed numbers are rejected.
    if any(line.strip() for line in lines[heading:first - 1] + lines[last:section["end_line"]]):
        return []
    from .question_identity import printed_anchors
    body = lines[heading:section["end_line"]]
    anchors = printed_anchors(body)
    printed = [number for _, number in anchors]
    if picture:
        if not anchors or not any(re.fullmatch(r"(?:\s*\[IMG\s+[^\]]+\])+\s*", line)
                                  for line in body[:anchors[0][0]]):
            return []
    if word_bank_heading or phonetic_write or dialogue_word:
        if not anchors or any(line.strip() for line in body[:anchors[0][0]]):
            return []
    if printed and sorted(printed) != sorted(int(n) for n in unit.get("numbers") or []):
        return []
    return [heading_text]


def section_rubric(lines, start_line, kind=""):
    """Return a complete instruction block before the first article in a section.

    Coordinates must refer to ``lines``. Never select instructions buried in a
    sibling's article/questions: only the block immediately following a section
    heading (or before the first A label) can be inherited.
    """
    from .extract import _plain
    from .preview import SECTION
    if kind and kind not in _TYPES:
        return []
    if type(start_line) is not int or not 1 <= start_line <= len(lines) + 1:
        return []
    stop = start_line - 1
    heading = None
    for i in range(stop - 1, -1, -1):
        if SECTION.match(_plain(lines[i]).strip()):
            heading = i
            break
    if heading is None:
        return []
    i = heading if instruction(lines[heading], kind) else heading + 1
    block = []
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if not instruction(line, kind):
            break
        block.append(line)
        i += 1
    # A numbered/local lead-in or article at section start is not a shared rubric.
    if not block or not re.search(r"阅读(?:下列|下面)短文", _plain(block[0])):
        return []
    return block
