"""Native-text lesson banners (A/B) only. No images, rendering or models.

The A基础巩固/B能力提升 banners in most worksheets are plain text lines such as
``A 基础巩固`` or ``B能力提升``. Only those printed lines are recognized here;
there is no visual column ownership, page rendering or image semantic analysis.
Image-only banners are simply not recognized and the consumer falls back to its
normal section/heading layout, never blocked for the missing visual evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

VERSION = "lesson-text-bands-v1"
ARTIFACT = "01_source/lesson-text-bands.json"
IMG = re.compile(r"\[IMG\s+([^\]]+)\]")


# 板块栏目标签全集：A/B 一对（基础巩固/能力提升）与同步练习册的
# 重要考点集训（考点/基础）/创新阅读专练（阅读提升）同属“板块容器横幅”。
FOUNDATION_LABELS = ("基础巩固", "重要考点集训")
EXTENSION_LABELS = ("能力提升", "创新阅读专练")
BAND_LABELS = FOUNDATION_LABELS + EXTENSION_LABELS

# 常见同步练习/试卷栏目板块标签（容器横幅，非大题标题、非题目正文）：
# 经典四类 + 常见分层/栏目横幅；命中时仅用于结构边界切分，不得混入题目正文。
COMMON_COLUMN_LABELS = BAND_LABELS + (
    "基础过关", "基础达标", "基础训练", "基础知识",
    "能力提高", "素养提升", "核心素养提升", "拓展提升", "拓展延伸", "培优提升",
    "课前预习", "课堂达标", "课后巩固", "课时精练", "语法精练", "语法聚焦",
    "考点突破", "考点集训", "中考链接", "真题演练", "中考真题演练",
    "综合提升", "综合训练", "分层训练", "语篇提能", "阅读提能",
    "写作专练", "新考法专练", "题型专练", "巩固提升", "达标训练", "过关训练",
)

# 非板块目录的常见答案/解析/附注/知识梳理/写作指导标签，动态目录抽取必须排除。
_NON_OUTLINE_LABELS = frozenset({
    "参考答案", "答案与解析", "参考答案与解析", "答案", "解析", "详解", "分析",
    "文章大意", "思路点拨", "参考范文", "优秀范文", "范文", "作文范文",
    "审题思路", "写作提纲", "写作要点", "要点", "注意事项", "要求", "参考词汇",
    "素材积累", "熟词生义", "点拨", "拓展", "引申",
    "考点", "词义辨析", "语法点", "注意", "易错点", "重难点", "词汇积累",
    "难句分析", "拓展设问", "考点小结", "知识小结", "语法小结", "知识梳理",
    "考点梳理", "语法梳理", "知识归纳", "考点归纳", "方法指导", "温馨提示",
    "解题技巧", "语法导图", "知识导图", "学习目标",
})
_NON_OUTLINE_SUFFIXES = ("小结", "梳理", "归纳", "导图", "提示", "技巧", "指导", "目标", "点拨", "积累", "辨析", "讲解", "要点", "提纲", "范文", "思路")
_BRACKETED_HEADING = re.compile(r"^[【\[]([^【】\[\]]+)[】\]]$")
_CHINESE_ORDINAL_PREFIX = re.compile(r"^[一二三四五六七八九十]+[.．、]\s*")
_STEP_PREFIX = re.compile(r"^(?:第\s*[0-9一二三四五六七八九十百零]+\s*(?:步|关|部分|环节|阶段)|Step\s*\d+)[.．、:：\s]*", re.I)
_QUESTION_OR_OPTION_LINE = re.compile(r"^\s*(?:[（(]\s*[）)]\s*\d+\s*[.．、)]|\d+\s*[.．、)]|[A-Ha-h]\s*[.．、]\s*\S)")


def normalize_band_text(text):
    """去掉排版装饰，得到可比较的横幅核心文字。

    容忍：Markdown 标题前缀 #、IMG 标记、Markdown 粗体 **、HTML 标签、内部空白、
    中文序号前缀（一、/二、/三、……）、外层括号/书名号包裹。这样
    **[IMG …]****一、重要考点集训****[IMG …]** 与 ## 【三、B 创新阅读专练】
    都会归一为可比较的核心文字（保留前导 A/B 以便做一致性校验）。
    """
    text = IMG.sub("", str(text or ""))
    text = re.sub(r"^\s*#{1,6}\s*", "", text)
    text = re.sub(r"<[^>]+>|\*|\s", "", text).strip("：:[]【】")
    # 去掉中文序号前缀（一、/二、/三、……），它是板块的印刷序号而非标签本体。
    text = re.sub(r"^[一二三四五六七八九十]+[.．、]?", "", text)
    return text


def strip_band_marker(text):
    """剥离可选的 A/B 板块标记，返回 (marker, label_body)。"""
    norm = normalize_band_text(text)
    match = re.fullmatch(r"([ABab])?[.．、:：]?(.*)", norm)
    if not match:
        return "", norm
    return (match.group(1) or "").upper(), match.group(2)


def band_label(text):
    """识别板块横幅标签，返回四个规范标签之一或空串（不校验 A/B 与标签一致性）。"""
    _, body = strip_band_marker(text)
    return body if body in BAND_LABELS else ""


def match_banner(text, *, extra_labels=()):
    """统一的板块横幅识别入口，返回 (label, kind)。

    - 经典四类标签：返回 (label, 'foundation' | 'extension' | 'invalid')；
      A/B 标记与标签本体冲突（如「A 能力提升」）返回 (label, 'invalid')。
    - 动态目录标签（仅限正文目录树里真实出现的 extra_labels）：
      剥离装饰与序号后精确命中时返回 (label, f'outline:{label}')。
    - 其余任意行一律返回 ('', '')，绝不把游离文字误判成板块横幅。
    """
    marker, body = strip_band_marker(text)
    if body in BAND_LABELS:
        kind_of = {"基础巩固": "A", "重要考点集训": "", "能力提升": "B", "创新阅读专练": ""}[body]
        if marker and kind_of and marker != kind_of:
            return body, "invalid"
        return body, ("foundation" if body in FOUNDATION_LABELS else "extension")
    if extra_labels and body in extra_labels:
        return body, f"outline:{body}"
    return "", ""


def extract_outline_heading(text):
    """从正文行抽取显式一级板块目录标题（供正文目录树动态对齐答案册）。

    只认两类有明确版面信号的标题：
    1. 命中经典四类板块标签（基础巩固/能力提升/重要考点集训/创新阅读专练）；
    2. 带中文方括号【…】或中文序号前缀（一、/二、/三、…）的 2–12 字纯中文标题
       （排除参考答案、解析、附注等非板块标签）。
    普通题干、英语句子、带标点长句均返回空串。
    """
    classic = band_label(text)
    if classic:
        return classic
    cleaned = IMG.sub("", str(text or ""))
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"<[^>]+>|\*", "", cleaned).strip()
    if not cleaned:
        return ""
    explicit = False
    bracket = _BRACKETED_HEADING.fullmatch(cleaned)
    if bracket:
        explicit = True
        inner = bracket.group(1).strip()
    else:
        inner = cleaned
    if _CHINESE_ORDINAL_PREFIX.match(inner):
        explicit = True
        inner = _CHINESE_ORDINAL_PREFIX.sub("", inner, count=1).strip()
    elif bracket and _STEP_PREFIX.match(inner):
        inner = _STEP_PREFIX.sub("", inner, count=1).strip()
    if not explicit:
        return ""
    inner = re.sub(r"^[ABＡＢab][.．、:：\s]*", "", inner)
    inner = re.sub(r"\s+", "", inner)
    if inner in _NON_OUTLINE_LABELS or inner.endswith(_NON_OUTLINE_SUFFIXES):
        return ""
    if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", inner):
        return inner
    return ""


def _is_step_banner(text) -> bool:
    """显式方括号步骤/环节横幅（如 `【第1步 词汇积累】`、`【第3步 典例分析】`、`【Step 1 ...】`），
    以及带中文一级序号的显式方括号栏目横幅（如 `【三、熟词生义】`）。"""
    cleaned = IMG.sub("", str(text or ""))
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"<[^>]+>|\*", "", cleaned).strip()
    bracket = _BRACKETED_HEADING.fullmatch(cleaned)
    if not bracket:
        return False
    inner = bracket.group(1).strip()
    if _STEP_PREFIX.match(inner):
        rest = re.sub(r"\s+", "", _STEP_PREFIX.sub("", inner, count=1))
        return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z]{2,16}", rest))
    if _CHINESE_ORDINAL_PREFIX.match(inner):
        rest = re.sub(r"\s+", "", _CHINESE_ORDINAL_PREFIX.sub("", inner, count=1))
        return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,12}", rest))
    return False


def strip_decorative_images(text) -> str:
    """统一剥离行内装饰图标记 `[IMG ...]`（文档大纲与标题比对唯一真相）。"""
    return re.sub(r"\s*\[IMG\s+[^\]]*\]", "", str(text or "")).strip()


def build_outline_ast(text_or_lines) -> list[dict]:
    """统一文档大纲解析（Outline AST）：单点产出容器横幅、步骤横幅、一级标题、大题标题与答案边界。

    下游模块（结构切分、答案骨架对齐、共享导语、回放诊断）共享同一份大纲判定口径，
    避免各自用零散正则对装饰图、横幅、步骤或答案边界得出不一致结论。
    """
    from . import sync_practice

    lines = text_or_lines.splitlines() if isinstance(text_or_lines, str) else list(text_or_lines or [])
    nodes: list[dict] = []
    active_band = ""
    active_band_kind = ""
    in_table = False
    for idx, raw in enumerate(lines, 1):
        clean = strip_decorative_images(raw)
        plain = sync_practice.plain_text(clean).strip()
        if plain.startswith("[TABLE"):
            in_table = True
            continue
        if in_table:
            if plain.startswith("[/TABLE"):
                in_table = False
            continue
        if not plain:
            continue
        if re.fullmatch(r"(?:参考答案|答案与解析|参考答案与解析)", plain):
            nodes.append({
                "line_no": idx, "raw": raw, "clean_text": clean, "plain": plain,
                "role": "answer_boundary", "band": "", "band_kind": "",
            })
            active_band = ""
            active_band_kind = ""
            continue
        hit = sync_practice.heading_hit(clean)
        if hit:
            outline = extract_outline_heading(clean)
            nodes.append({
                "line_no": idx, "raw": raw, "clean_text": clean, "plain": plain,
                "role": "section_heading",
                "band": outline or active_band,
                "band_kind": f"outline:{outline}" if outline else active_band_kind,
                "rule": hit.get("rule") or "",
                "type": hit.get("type") or "",
                "kind": hit.get("kind") or "",
            })
            continue
        label, kind = match_banner(clean)
        if label and kind != "invalid":
            active_band = label
            active_band_kind = kind
            nodes.append({
                "line_no": idx, "raw": raw, "clean_text": clean, "plain": plain,
                "role": "column_banner", "band": label, "band_kind": kind,
            })
            continue
        if _is_step_banner(clean):
            outline = extract_outline_heading(clean)
            active_band = outline or plain
            active_band_kind = f"outline:{outline}" if outline else "step"
            nodes.append({
                "line_no": idx, "raw": raw, "clean_text": clean, "plain": plain,
                "role": "step_banner", "band": active_band, "band_kind": active_band_kind,
            })
            continue
        if is_column_banner(clean):
            outline = extract_outline_heading(clean)
            if outline:
                active_band = outline
                active_band_kind = f"outline:{outline}"
            nodes.append({
                "line_no": idx, "raw": raw, "clean_text": clean, "plain": plain,
                "role": "outline_heading" if outline else "column_banner",
                "band": outline or plain, "band_kind": f"outline:{outline}" if outline else "column",
            })
    return nodes


def is_column_banner(text) -> bool:
    """通用栏目/板块容器横幅判定（非大题标题、非题干/选项正文）。

    用于切分大题窗口与过滤题目预览，确保各类栏目板块横幅（如 `【二、A 基础巩固】`、
    `【三、B 能力提升】`、`【第3步 典例分析】`、`一、重要考点集训`、`二、素养提升`、`综合阅读提升` 等）
    只作为容器边界，绝不混入上一题或下一题的题干/材料正文。
    真实大题标题（如 `一、教材语篇回顾`、`Ⅰ. 单项填空`）优先归为大题，不判为纯栏目横幅。
    """
    raw = str(text or "")
    stripped = raw.strip()
    if not stripped or stripped.startswith(("[TABLE", "[/TABLE")):
        return False
    from . import sync_practice
    plain = sync_practice.plain_text(raw).strip()
    if not plain or _QUESTION_OR_OPTION_LINE.match(plain):
        return False
    if sync_practice.heading_hit(raw):
        return False
    if band_label(raw):
        return True
    _, body = strip_band_marker(raw)
    if body in COMMON_COLUMN_LABELS:
        return True
    if _is_step_banner(raw):
        return True
    if sync_practice.is_section_title(plain):
        return True
    return bool(extract_outline_heading(raw))


def canonical_label(text):
    """原生正文 A/B 板块标签（仅 基础巩固/能力提升），校验 A/B 与标签一致。

    这里只认 A/B 一对（正文层级），不返回 重要考点集训/创新阅读专练——那是答案册
    层级，由 band_label / match_banner 供 answers 侧使用，避免把正文 A/B 语义扩大到答案册容器。
    """
    marker, body = strip_band_marker(text)
    if body not in ("基础巩固", "能力提升"):
        return ""
    if marker and marker != ("A" if body == "基础巩固" else "B"):
        return ""
    return body


def _stop(line):
    from . import sync_practice
    plain = sync_practice.plain_text(line).strip()
    return (sync_practice.is_section_title(plain)
            or _is_step_banner(line)
            or bool(sync_practice._LESSON.match(plain) or sync_practice._UNIT.match(plain))
            or bool(re.fullmatch(r"(?:参考答案|答案与解析|参考答案与解析)", plain)))


def _boundaries(text):
    lines = text.splitlines()
    from .sync_practice import answer_key_start
    appendix = answer_key_start(text)
    rows = []
    for index, line in enumerate(lines, 1):
        if appendix and index >= appendix:
            break
        label = canonical_label(line)
        if not label:
            continue
        rows.append({"label": label, "marker": "A" if label == "基础巩固" else "B",
                     "start_line": index, "image_name": None, "image_sha256": None,
                     "evidence": {"method": "native_text", "text": line}})
    for pos, row in enumerate(rows):
        end = rows[pos + 1]["start_line"] - 1 if pos + 1 < len(rows) else (appendix - 1 if appendix else len(lines))
        for cursor in range(row["start_line"], end):
            if _stop(lines[cursor]) or canonical_label(lines[cursor]):
                end = cursor
                break
        row["end_line"] = end
        row["band_id"] = "band-" + hashlib.sha256(
            f"{row['start_line']}:{row['label']}".encode()).hexdigest()[:16]
    return rows


def for_text(root, text):
    """Return native-text bands for the given body view; always safe to call."""
    text = text if isinstance(text, str) else ""
    return {"version": VERSION, "status": "native_text", "bands": _boundaries(text),
            "features": [], "coordinate_basis": "consumer_text"}


def load_verified(root, text=None):
    """Kept for callers that only need the current native-text bands."""
    if text is None:
        try:
            text = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
        except OSError:
            return {"version": VERSION, "status": "unavailable", "bands": [], "features": [], "issues": []}
    return for_text(root, text)


def annotate_sections(sections, evidence):
    rows = deepcopy(sections)
    bands = (evidence or {}).get("bands") or []
    for row in rows:
        start = row.get("start_line") or row.get("source_line")
        owners = [b for b in bands if type(start) is int and b["start_line"] <= start <= b["end_line"]]
        if len(owners) == 1:
            band = owners[0]
            row["lesson_band"] = {k: deepcopy(band.get(k)) for k in
                                  ("band_id", "label", "start_line", "end_line", "image_name", "image_sha256")}
        if type(start) is int:
            stops = [b["start_line"] - 1 for b in bands if b["start_line"] > start]
            if owners:
                stops.append(owners[0]["end_line"])
            if stops:
                end = row.get("end_line") or row.get("source_end_line")
                stop = min(stops + ([end] if type(end) is int else []))
                row["lesson_container_end_line"] = stop
                if "start_line" in row:
                    row["end_line"] = stop
                if "source_line" in row:
                    row["source_end_line"] = stop
    return rows


def build(root, *, force=False, identify=None):
    """No-op compatibility shim: native text needs no prebuilt artifact."""
    try:
        text = (root / "01_source/正文.txt").read_text(encoding="utf-8-sig")
    except OSError:
        return {"status": "unavailable", "bands": 0, "model_calls": 0}
    return {"status": "native_text", "bands": len(_boundaries(text)), "model_calls": 0}
