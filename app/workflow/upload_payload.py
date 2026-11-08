"""Build a read-only, allowlisted POST /api/uploads staging payload.

No network, media deployment, paper creation or workflow state changes happen here.
A failed report may contain a safe candidate for inspection; callers MUST require
``passed`` before freezing or sending it. Catalog snapshots are supplied by the
client after fetching the active general version and all its points.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from html import unescape
import json
import math
import re
from urllib.parse import unquote, urlsplit

from workbench import taxonomy
from workflow import label_audit
from workflow.metadata_policy import MISSING

SOURCE_ALIASES = {"精品资料": "精品题源", "模拟卷": "中考模拟", "历年考题": "中考真题", "课本原创": "课本原创"}
TYPE_ALIASES = {"单项选择": "单项填空", "短对话理解": "听力短对话理解", "长对话理解": "听力长对话理解",
                "短文理解": "听力短文理解", "信息转换": "听力信息转换"}
AI_MARK = "（AI生成，待校对）"
DIFFICULTIES = {"容易": "易", "适中": "中", "困难": "难", "易": "易", "中": "中", "难": "难"}
SOURCE_FIELDS = ("source_type", "source", "location", "version", "authorization", "year", "institution", "school", "regional_version")
FILTER_FIELDS = {"subtypes", "categories", "region", "city", "district", "semesters", "difficultyValue"}
ALIASES = {"question_type": "type", "filter_metadata": "filterMetadata", "knowledgeIds": "knowledge_ids",
           "material_blocks": "materialBlocks", "article_summary": "articleSummary", "textbook_mappings": "textbookMappings"}
WIRE_FIELDS = {"id", "title", "unit_kind", "stem", "options", "answer", "explanation", "curriculum", "textbook_id",
               "knowledge_version_id", "knowledge_ids", "knowledge", "type", "difficulty", "filterMetadata",
               "material", "materialBlocks", "articleSummary", "subquestions", "attachments", "relations", *SOURCE_FIELDS}
# Explicit local bookkeeping is deliberately excluded from the API request.
LOCAL_FIELDS = {"unit_id", "number", "numbers", "status", "warnings", "knowledgeStatus", "human_confirmed",
                "evidence", "source_slice", "source_parent_id", "source_numbers", "review", "review_status",
                "checked", "notes", "wordCount", "word_count", "materialBlocksLocked", "filterMetadataStored",
                "optionColumns", "subquestionOptionColumns", "paperId", "paperOrder", "paperSection", "paperInstruction",
                "numberStart", "numberEnd", "subtypes", "categories", "audio", "images", "image", "audio_map", "image_map"}


def _path(value):
    # Do not split: a real leaf name can contain '/', for example 谚语/习语.
    return re.sub(r"\s*/\s*", "/", value.strip())


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _difficulty(value):
    return DIFFICULTIES.get(value, value) if isinstance(value, str) else value


def _local_audit_view(batch):
    """Normalize declared transport aliases without aggregating or remapping IDs."""
    result = deepcopy(batch)

    def visit(item):
        if not isinstance(item, dict):
            return
        for alias, key in ALIASES.items():
            if alias in item and key not in item:
                item[key] = deepcopy(item[alias])
        if isinstance(item.get("type"), str):
            item["type"] = TYPE_ALIASES.get(item["type"], item["type"])
        if isinstance(item.get("source_type"), str):
            item["source_type"] = SOURCE_ALIASES.get(item["source_type"], item["source_type"])
        filters = item.get("filterMetadata", {})
        if "difficulty" not in item and isinstance(filters, dict) and "difficulty" in filters:
            item["difficulty"] = filters["difficulty"]
        difficulty = item.get("difficulty")
        if isinstance(difficulty, str):
            item["difficulty"] = {"易": "容易", "中": "适中", "难": "困难"}.get(difficulty, difficulty)
        paths = item.get("knowledge")
        if isinstance(paths, list):
            item["knowledge"] = [_path(p) if isinstance(p, str) else p for p in paths]
        if isinstance(item.get("subquestions"), list):
            for child in item["subquestions"]:
                visit(child)

    if isinstance(result.get("items"), list):
        for item in result["items"]:
            visit(item)
    return result


def _safe_blocks(blocks):
    """Preserve supported structure while preventing unknown nested controls leaking."""
    def list_items(items):
        if not isinstance(items, list):
            return []
        return [({k: (list_items(v) if k == "items" else deepcopy(v)) for k, v in item.items() if k in {"text", "items"}}
                 if isinstance(item, dict) else deepcopy(item)) for item in items]

    if not isinstance(blocks, list):
        return []
    result = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        safe = {k: deepcopy(v) for k, v in block.items() if k in {"type", "variant"}}
        safe["rows"] = []
        for row in block.get("rows", []) if isinstance(block.get("rows"), list) else []:
            if not isinstance(row, dict):
                continue
            cells = []
            for cell in row.get("cells", []) if isinstance(row.get("cells"), list) else []:
                if isinstance(cell, dict):
                    cells.append({k: (list_items(v) if k == "items" else deepcopy(v)) for k, v in cell.items()
                                  if k in {"text", "items", "rowspan", "colspan", "header"}})
            safe["rows"].append({"cells": cells})
        result.append(safe)
    return result


def _media_path(url):
    """Return a decoded safe source-media path, or None for unsafe URLs."""
    if not isinstance(url, str):
        return None
    decoded = url
    for _ in range(8):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    try:
        parts = urlsplit(decoded)
        if (decoded.startswith('/source-media/') and len(decoded) > len('/source-media/')
                and not decoded.endswith('/') and not parts.scheme and not parts.netloc
                and not parts.query and not parts.fragment and '?' not in decoded and '#' not in decoded
                and '\\' not in decoded and '//' not in decoded
                and all(part not in ('.', '..') for part in decoded.split('/'))
                and not any(ord(c) < 32 or ord(c) == 127 for c in decoded)
                and '%' not in decoded
                and decoded.count('/') == url.count('/')):
            return decoded
    except ValueError:
        pass
    return None


class _Builder:
    def __init__(self, media_map=None):
        self.media_map = media_map if isinstance(media_map, dict) else None
        self.media_sources = {}
        self.errors = []
        self.warnings = []
        self.summary = {"units": 0, "questions": 0, "attachments": 0}
        self.version = None
        self.paths = {}
        self.remote_ids = {}
        self.leaves = set()
        self.catalog_sha256 = None

    def error(self, code, message, uid=""):
        self.errors.append({"code": code, "message": message, "unit_id": str(uid)})

    def warning(self, code, message, uid=""):
        self.warnings.append({"code": code, "message": message, "unit_id": str(uid)})

    def catalog(self, snapshot):
        if snapshot is None:
            self.error("CATALOG_REQUIRED", "上传前必须读取当前启用通用知识点版本及完整节点快照")
            return
        try:
            content = {"version": snapshot.get("version"), "points": snapshot.get("points")} if isinstance(snapshot, dict) else snapshot
            canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            self.catalog_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            self.error("CATALOG_INVALID", "目录快照必须是可序列化的 JSON")
            return
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("version"), dict) or not isinstance(snapshot.get("points"), list):
            self.error("CATALOG_INVALID", "目录快照需要 version 对象和 points 数组")
            return
        version = snapshot["version"]
        if (not _nonempty(version.get("id")) or version.get("textbook_id") not in (None, "")
                or not (version.get("status") == "active" or version.get("active") is True)
                or version.get("active") is False or version.get("status", "active") != "active"):
            self.error("CATALOG_VERSION_INVALID", "目录必须为有 ID 的启用通用版本（非教材专用版本）")
            return
        self.version = version["id"]
        nodes = {}
        for point in snapshot["points"]:
            if not isinstance(point, dict) or not _nonempty(point.get("id")) or not _nonempty(point.get("name")):
                self.error("CATALOG_POINT_INVALID", "每个目录节点必须有字符串 id 和 name")
                continue
            pid = point["id"]
            if pid in nodes:
                self.error("CATALOG_POINT_DUPLICATE", f"目录 ID 重复：{pid}")
                continue
            point = deepcopy(point)
            if "active" in point:
                flag = point["active"]
                # 当前 API 直接返回 SQLite 的 0/1；只兼容精确整数，不放宽到字符串/浮点数。
                if type(flag) is int and flag in (0, 1):
                    point["active"] = bool(flag)
                elif not isinstance(flag, bool):
                    self.error("CATALOG_POINT_INVALID", f"目录节点 active 必须是布尔值或整数 0/1：{pid}")
            if point.get("version_id", self.version) != self.version:
                self.error("CATALOG_POINT_VERSION", f"目录节点版本不一致：{pid}")
            if version.get("subject_id") and point.get("subject_id", version["subject_id"]) != version["subject_id"]:
                self.error("CATALOG_SUBJECT_MISMATCH", f"目录节点不属于当前科目：{pid}")
            if point.get("parent_id") is not None and not isinstance(point.get("parent_id"), str):
                self.error("CATALOG_PARENT_INVALID", f"目录 parent_id 必须是字符串或 null：{pid}")
                continue
            nodes[pid] = point
        resolved, visiting = {}, set()

        def resolve(pid):
            if pid in resolved:
                return resolved[pid]
            if pid in visiting:
                raise ValueError(f"目录存在循环：{pid}")
            if pid not in nodes:
                raise ValueError(f"目录缺父节点：{pid}")
            visiting.add(pid)
            node = nodes[pid]
            parent = node.get("parent_id")
            prefix, parent_active = resolve(parent) if parent else ("", True)
            active = parent_active and node.get("active") is not False
            value = ((_path(prefix + "/" + node["name"]) if prefix else _path(node["name"])), active)
            visiting.remove(pid)
            resolved[pid] = value
            return value

        for pid in nodes:
            try:
                path, active = resolve(pid)
            except ValueError as exc:
                self.error("CATALOG_TREE_INVALID", str(exc))
                visiting.clear()
                continue
            if active:
                self.paths.setdefault(path, []).append(pid)
                self.remote_ids[pid] = path
        parents = {node.get("parent_id") for node in nodes.values()}
        self.leaves = set(self.remote_ids) - parents
        if not self.remote_ids:
            self.error("CATALOG_EMPTY", "启用目录中没有可用节点")

    def unknown(self, data, allowed, uid, context):
        for key in data:
            if key not in allowed:
                self.error("UNKNOWN_FIELD", f"{context}未知字段 {key}；需明确映射，不能静默丢弃", uid)

    def canonical(self, item, uid):
        data = deepcopy(item)
        for alias, key in ALIASES.items():
            if alias in data:
                if key in data and data[key] != data[alias]:
                    self.error("ALIAS_CONFLICT", f"{alias} 与 {key} 不一致", uid)
                else:
                    data[key] = data[alias]
                del data[alias]
        filters = data.get("filterMetadata", {})
        if not isinstance(filters, dict):
            self.error("FILTER_INVALID", "filterMetadata 必须是对象", uid)
            filters = {}
        self.unknown(filters, FILTER_FIELDS | set(SOURCE_FIELDS) | {"difficulty"}, uid, "filterMetadata.")
        for key in (*SOURCE_FIELDS, "difficulty"):
            if key in filters:
                left, right = data.get(key), filters[key]
                if key == "difficulty":
                    left, right = _difficulty(left), _difficulty(right)
                if key in data and left != right:
                    self.error("ALIAS_CONFLICT", f"{key} 与 filterMetadata.{key} 不一致", uid)
                else:
                    data[key] = filters[key]
        for key in ("subtypes", "categories"):
            if key in data:
                if key in filters and filters[key] != data[key]:
                    self.error("ALIAS_CONFLICT", f"{key} 与 filterMetadata.{key} 不一致", uid)
                else:
                    filters[key] = data[key]
        if "filterMetadata" in data or filters:
            data["filterMetadata"] = {k: v for k, v in filters.items() if k in FILTER_FIELDS}
        return data

    def knowledge(self, data, output, uid, allow_empty=False):
        paths = data.get("knowledge")
        if paths is None and allow_empty:
            output["knowledge"] = []
            return
        if not isinstance(paths, list) or any(not _nonempty(p) for p in paths):
            self.error("KNOWLEDGE_REQUIRED", "knowledge 必须是完整叶子路径数组", uid)
            return
        if not paths:
            # 整篇/整组题型只在组级打知识点；子题为空是本地既定规则。
            if allow_empty:
                output["knowledge"] = []
                return
            self.error("KNOWLEDGE_REQUIRED", "knowledge 必须是非空完整叶子路径数组", uid)
            return
        paths = [_path(p) for p in paths]
        if len(paths) != len(set(paths)):
            self.error("KNOWLEDGE_DUPLICATE", "knowledge 完整路径不能重复", uid)
        output["knowledge"] = paths
        if "knowledge_version_id" in data and data["knowledge_version_id"] != self.version and self.version:
            self.error("KNOWLEDGE_VERSION_MISMATCH", "显式 knowledge_version_id 与当前远端通用版本不一致", uid)
        if not self.version:
            output.pop("knowledge_ids", None)
            output.pop("knowledge_version_id", None)
            return
        ids = []
        for path in paths:
            matches = self.paths.get(path, [])
            if not matches:
                self.error("REMOTE_KNOWLEDGE_MISSING", f"远端目录无完整路径：{path}", uid)
            elif len(matches) != 1:
                self.error("REMOTE_KNOWLEDGE_AMBIGUOUS", f"远端完整路径不唯一：{path}", uid)
            elif matches[0] not in self.leaves:
                self.error("REMOTE_KNOWLEDGE_NOT_LEAF", f"远端路径不是叶子：{path}", uid)
            else:
                ids.append(matches[0])
        output["knowledge_ids"] = list(dict.fromkeys(ids))
        output["knowledge_version_id"] = self.version

    def mappings(self, data, output, uid):
        if "textbook_id" in data and not _nonempty(data["textbook_id"]):
            self.error("TEXTBOOK_ID_INVALID", "textbook_id 必须是非空字符串", uid)
        if "textbookMappings" not in data:
            return
        mappings = data["textbookMappings"]
        if not isinstance(mappings, list):
            self.error("TEXTBOOK_MAPPINGS_INVALID", "textbookMappings 必须是数组", uid)
            return
        for mapping in mappings:
            if not isinstance(mapping, dict):
                self.error("TEXTBOOK_MAPPINGS_INVALID", "每项教材映射必须是对象", uid)
                continue
            self.unknown(mapping, {"textbookId", "knowledgeVersionId", "knowledgeIds", "curriculum"}, uid, "textbookMappings.")
            if not _nonempty(mapping.get("textbookId")):
                self.error("TEXTBOOK_ID_INVALID", "教材映射缺少字符串 textbookId", uid)
            if "curriculum" in mapping and not isinstance(mapping["curriculum"], str):
                self.error("TEXTBOOK_MAPPINGS_INVALID", "教材映射 curriculum 必须是字符串", uid)
            version = mapping.get("knowledgeVersionId")
            ids = mapping.get("knowledgeIds", [])
            if version != self.version or not self.version:
                self.error("TEXTBOOK_CATALOG_UNSUPPORTED", "教材映射所用版本缺少对应目录快照；当前仅有通用版本，不能丢弃或改绑映射", uid)
            if not isinstance(ids, list) or any(not _nonempty(i) for i in ids) or len(ids) != len(set(i for i in ids if isinstance(i, str))):
                self.error("TEXTBOOK_MAPPINGS_INVALID", "教材映射 knowledgeIds 必须是无重复的 ID 数组", uid)
            elif any(i not in self.leaves for i in ids):
                self.error("TEXTBOOK_KNOWLEDGE_INVALID", "教材映射 ID 必须是所选远端版本的叶子节点", uid)
        # Preserve all mappings, including repeated textbook positions, without rewriting IDs.
        output["textbookMappings"] = [{k: deepcopy(v) for k, v in mapping.items()
                                      if k in {"textbookId", "knowledgeVersionId", "knowledgeIds", "curriculum"}}
                                     for mapping in mappings if isinstance(mapping, dict)]

    def collect_media_sources(self, value):
        """Collect distinct source URLs before mapping, including child attachments."""
        if isinstance(value, dict):
            attachments = value.get('attachments')
            if isinstance(attachments, list):
                for attachment in attachments:
                    url = attachment.get('url') if isinstance(attachment, dict) else None
                    path = _media_path(url)
                    if path:
                        self.media_sources.setdefault(path.rsplit('/', 1)[-1], set()).add(url)
            for key, child in value.items():
                if key != 'attachments':
                    self.collect_media_sources(child)
        elif isinstance(value, list):
            for child in value:
                self.collect_media_sources(child)

    def attachments(self, attachments, uid, remap=True):
        if not isinstance(attachments, list):
            self.error("ATTACHMENTS_INVALID", "attachments 必须是数组", uid)
            return []
        result = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                self.error("ATTACHMENT_INVALID", "附件必须是对象", uid)
                continue
            self.unknown(attachment, {"kind", "url", "name", "placement", "optionKey"}, uid, "attachments.")
            safe = {k: deepcopy(v) for k, v in attachment.items() if k in {"kind", "url", "name", "placement", "optionKey"}}
            result.append(safe)
            self.summary["attachments"] += 1
            if attachment.get("kind") not in ("audio", "image"):
                self.error("ATTACHMENT_KIND_INVALID", "附件 kind 仅允许 audio/image", uid)
            url = attachment.get("url")
            path = _media_path(url)
            if path and remap and self.media_map is not None:
                name = path.rsplit('/', 1)[-1]
                if name in self.media_map:
                    if len(self.media_sources.get(name, ())) > 1:
                        self.error("MEDIA_MAP_AMBIGUOUS", f"媒体文件名对应多个来源路径：{name}", uid)
                    else:
                        safe["url"] = self.media_map[name]
            if not _media_path(safe.get("url")):
                self.error("ATTACHMENT_URL_INVALID", "附件必须使用已部署的相对 /source-media/ 文件路径；禁止外链、本地路径和路径穿越", uid)
                safe.pop("url", None)
            if "name" in attachment and not isinstance(attachment["name"], str):
                self.error("ATTACHMENT_NAME_INVALID", "附件 name 必须是字符串", uid)
            placement = attachment.get("placement")
            if placement is not None and placement not in ("question", "stem", "option", "answer", "explanation", "material"):
                self.error("ATTACHMENT_PLACEMENT_INVALID", "附件 placement 不是已支持位置", uid)
            if "optionKey" in attachment and (not isinstance(attachment["optionKey"], str) or not re.fullmatch(r"[A-Z]", attachment["optionKey"])):
                self.error("ATTACHMENT_OPTION_INVALID", "optionKey 必须是单个大写 A–Z", uid)
            if placement == "option" and "optionKey" not in attachment:
                self.error("ATTACHMENT_OPTION_INVALID", "选项附件必须声明 optionKey", uid)
            if "optionKey" in attachment and placement != "option":
                self.error("ATTACHMENT_OPTION_INVALID", "optionKey 仅用于 placement=option", uid)
        return result

    def blocks(self, blocks, uid):
        if not isinstance(blocks, list):
            self.error("MATERIAL_BLOCKS_INVALID", "materialBlocks 必须是数组", uid)
            return
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "table":
                self.error("MATERIAL_BLOCKS_INVALID", "目前仅支持结构化 table 材料", uid)
                continue
            self.unknown(block, {"type", "variant", "rows"}, uid, "materialBlocks.")
            if "variant" in block and block["variant"] not in ("information-conversion", "poster"):
                self.error("MATERIAL_BLOCKS_INVALID", "表格 variant 不在文档支持范围", uid)
            rows = block.get("rows")
            if not isinstance(rows, list) or not rows:
                self.error("MATERIAL_TABLE_INVALID", "表格需要非空 rows", uid)
                continue
            occupied = set()
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict) or not isinstance(row.get("cells"), list):
                    self.error("MATERIAL_TABLE_INVALID", "表格每行必须有 cells 数组", uid)
                    continue
                self.unknown(row, {"cells"}, uid, "table.rows.")
                column = 0
                for cell in row["cells"]:
                    if not isinstance(cell, dict):
                        self.error("MATERIAL_TABLE_INVALID", "表格 cell 必须是对象", uid)
                        continue
                    self.unknown(cell, {"text", "items", "rowspan", "colspan", "header"}, uid, "table.cells.")
                    if "text" in cell and not isinstance(cell["text"], str):
                        self.error("MATERIAL_TABLE_INVALID", "单元格 text 必须是字符串", uid)
                    if "header" in cell and not isinstance(cell["header"], bool):
                        self.error("MATERIAL_TABLE_INVALID", "单元格 header 必须是布尔值", uid)
                    if "items" in cell:
                        self.list_items(cell["items"], uid)
                    rs, cs = cell.get("rowspan", 1), cell.get("colspan", 1)
                    if not _positive(rs) or not _positive(cs) or rs > len(rows) or cs > 1000:
                        self.error("MATERIAL_TABLE_SPAN_INVALID", "合并跨度必须是正整数且不超过表格边界", uid)
                        continue
                    while (row_index, column) in occupied:
                        column += 1
                    rectangle = {(r, c) for r in range(row_index, row_index + rs) for c in range(column, column + cs)}
                    if row_index + rs > len(rows) or rectangle & occupied:
                        self.error("MATERIAL_TABLE_OVERLAP", "单元格合并重叠或跨出末行", uid)
                    occupied.update(rectangle)
                    column += cs
            widths = [{c for r, c in occupied if r == index} for index in range(len(rows))]
            if not widths or not widths[0] or any(cols != widths[0] for cols in widths):
                self.error("MATERIAL_TABLE_GRID_INVALID", "表格网格存在空洞或行宽不一致", uid)

    def list_items(self, items, uid):
        if not isinstance(items, list):
            self.error("MATERIAL_LIST_INVALID", "单元格 items 必须是列表", uid)
            return
        for item in items:
            if isinstance(item, str):
                continue
            if not isinstance(item, dict):
                self.error("MATERIAL_LIST_INVALID", "列表项需要字符串或 {text,items} 对象", uid)
                continue
            self.unknown(item, {"text", "items"}, uid, "table.items.")
            if "text" in item and not isinstance(item["text"], str):
                self.error("MATERIAL_LIST_INVALID", "列表项 text 必须是字符串", uid)
            if "items" in item:
                self.list_items(item["items"], uid)

    def options(self, values, uid):
        if not isinstance(values, list):
            self.error("OPTIONS_INVALID", "options 必须是数组", uid)
            return []
        result, labels = [], set()
        for option in values:
            if isinstance(option, str):
                result.append(option)
            elif isinstance(option, dict):
                self.unknown(option, {"label", "text", "key"}, uid, "options.")
                label, text = option.get("label", option.get("key")), option.get("text")
                if not isinstance(label, str) or not re.fullmatch(r"[A-Z]", label) or label in labels or not isinstance(text, str):
                    self.error("OPTION_INVALID", "对象选项需要唯一的大写 label 和字符串 text", uid)
                else:
                    labels.add(label)
                    result.append(f"{label}. {text}")
            else:
                self.error("OPTION_INVALID", "选项必须是字符串或 {label,text}", uid)
        return result

    def scan_media(self, value, uid):
        if isinstance(value, str):
            if re.search(r"\[IMG\b[^\]]*\]|<\s*/?\s*(?:img|audio|video|source)\b|!\[[^\]]*\]|file://|data:(?:image|audio|video)/|data:[^,]*;base64,", value, re.I):
                self.error("UNRESOLVED_MEDIA_REFERENCE", "媒体只能通过已部署的 attachments 关联，禁止内嵌 HTML/Markdown 媒体或本地引用", uid)
        elif isinstance(value, dict):
            for child in value.values():
                self.scan_media(child, uid)
        elif isinstance(value, list):
            for child in value:
                self.scan_media(child, uid)

    def item(self, raw, uid, child=False, parent_type=None):
        if not isinstance(raw, dict):
            self.error("ITEM_INVALID", "题目/子题必须是结构化对象", uid)
            return {}
        self.unknown(raw, WIRE_FIELDS | set(ALIASES) | LOCAL_FIELDS | {"textbookMappings"}, uid, "子题." if child else "题目.")
        data = self.canonical(raw, uid)
        # 服务端接受旧称/简称；本地在映射入口就归一，后续标签校验与落库都用正式名。
        if isinstance(data.get("type"), str):
            data["type"] = TYPE_ALIASES.get(data["type"], data["type"])
        if isinstance(data.get("source_type"), str):
            data["source_type"] = SOURCE_ALIASES.get(data["source_type"], data["source_type"])
        # 用户规则：缺答案/缺解析不阻断，显式填「无答案」「无解析」占位。
        if not _nonempty(data.get("answer")) and not (
                not child and data.get("unit_kind") == "group" and data.get("subquestions")):
            data["answer"] = "无答案"
        if not _nonempty(data.get("explanation")):
            data["explanation"] = "无解析"
        # 用户规则：AI 生成的解析/大意一律不采用，按缺失处理，不再做 AI 标记校验。
        for key in ("explanation", "articleSummary"):
            if isinstance(data.get(key), str) and ("AI生成" in data[key] or "AI 生成" in data[key]):
                data[key] = ""
        output = {k: deepcopy(v) for k, v in data.items() if k in WIRE_FIELDS or k == "textbookMappings"}
        if child:
            output["number"] = raw.get("number")
            if not _positive(raw.get("number")):
                self.error("SUBQUESTION_NUMBER_INVALID", "子题 number 必须是原卷正整数题号", uid)
        if "id" in data and not _nonempty(data["id"]):
            self.error("QUESTION_ID_INVALID", "id 必须是非空字符串", uid)
        # Local groups own answers on their children. A transport-only aggregate
        # may be created later, after exact child-number/answer validation.
        deferred_group_answer = (not child
            and data.get("unit_kind", "group" if data.get("subquestions") else "standalone") == "group"
            and (data.get("answer") is None
                 or isinstance(data.get("answer"), str) and not data["answer"].strip()))
        material_group = (not child and data.get('unit_kind') == 'group'
                          and bool(data.get('subquestions'))
                          and (_nonempty(data.get('material')) or bool(data.get('materialBlocks'))))
        for key in ("stem", "answer"):
            if not _nonempty(data.get(key)) and not (
                    key == "answer" and deferred_group_answer or key == 'stem' and material_group):
                self.error("MISSING_" + key.upper(), f"缺少 {key} 正文", uid)
        for key, prefix in (("answer", r"(?:参考答案|答案|answer)"), ("explanation", r"(?:解析|explanation)")):
            text = data.get(key)
            if isinstance(text, str):
                # Inspect formatting without rewriting source content on the wire.
                text = unescape(re.sub(r"</?(?:b|strong|i|em|u|sup|sub|span|p|div|br|font|small|mark)\b[^>]*>", "", text, flags=re.I))
                if re.match(r"\s*[*_`~]*\s*" + prefix + r"\s*[*_`~]*\s*[:：]", text, re.I):
                    self.error("CONTENT_PREFIX_INVALID", f"{key} 只保留正文，不重复字段标题前缀", uid)
        # 用户规则：缺文章大意填「无」，不生成；AI 生成文本在上方已丢弃。
        if not _nonempty(data.get("explanation")):
            if data.get("explanation") is not None and not isinstance(data.get("explanation"), str):
                self.error("EXPLANATION_INVALID", "explanation 必须是字符串", uid)
            self.warning("MISSING_EXPLANATION", "来源未提供解析；按用户规则应在字段填「无」（不生成）", uid)
        for key in ("title", "material", "articleSummary", "curriculum", "explanation"):
            if key in data and not isinstance(data[key], str):
                self.error("TEXT_FIELD_INVALID", f"{key} 必须是字符串", uid)
        if not child:
            if isinstance(data.get("source_type"), str):
                data["source_type"] = SOURCE_ALIASES.get(data["source_type"], data["source_type"])
            output.setdefault("articleSummary", "")
            for key in ("source_type", "source"):
                if not _nonempty(data.get(key)):
                    self.error("MISSING_" + key.upper(), f"缺少显式来源字段 {key}，不得推定或补占位值", uid)
            # 服务端对以下字段有默认值（空串/待核实），不因本地未提供而阻断上传；
            # 仍保留警告，提醒编辑在草稿里补齐。
            for key in ("location", "version", "authorization"):
                if not _nonempty(data.get(key)):
                    self.warning("SOURCE_FIELD_EMPTY", f"未提供 {key}；服务端允许留空，建议编辑补齐", uid)
            if data.get("source_type") not in [*taxonomy.sources(), "精品题源", "课本原创"]:
                self.error("SOURCE_TYPE_INVALID", "source_type 不在正式题源目录中", uid)
            if data.get("source_type") in ("中考真题", "中考模拟") and not (_positive(data.get("year")) and data["year"] <= 9999):
                self.error("MISSING_YEAR", "真题/模拟需要有来源证据的整数年份", uid)
            if data.get("source_type") == "中考模拟":
                for key in ("institution", "school", "regional_version"):
                    if not _nonempty(data.get(key)):
                        self.error("MISSING_" + key.upper(), f"中考模拟缺少 {key}", uid)
        if "year" in data and not (_positive(data["year"]) and data["year"] <= 9999):
            self.error("YEAR_INVALID", "显式 year 必须是有来源证据的正整数年份", uid)
        if child and "source_type" in data and data["source_type"] not in [*taxonomy.sources(), "精品题源", "课本原创"]:
            self.error("SOURCE_TYPE_INVALID", "子题显式 source_type 不在正式或兼容题源中", uid)
        for key in SOURCE_FIELDS:
            if key in data and key != "year" and not isinstance(data[key], str):
                self.error("SOURCE_FIELD_INVALID", f"{key} 必须是字符串", uid)
        difficulty = data.get("difficulty")
        if not child or difficulty is not None:
            if not isinstance(difficulty, str) or difficulty not in DIFFICULTIES:
                self.error("DIFFICULTY_INVALID", "难度需要显式容易/适中/困难或易/中/难", uid)
            else:
                output["difficulty"] = DIFFICULTIES[difficulty]
        filters = output.get("filterMetadata", {})
        # Historical local batches use this marker for a missing category. Remove
        # only the exact marker on the wire; other values still require validation.
        if isinstance(filters.get("categories"), list):
            filters["categories"] = [category for category in filters["categories"] if category != MISSING]
        if "difficultyValue" in filters and filters["difficultyValue"] is not None:
            value = filters["difficultyValue"]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                self.error("DIFFICULTY_VALUE_INVALID", "difficultyValue 必须是 0–1 有限数值或 null", uid)
            elif isinstance(difficulty, str) and difficulty in DIFFICULTIES and DIFFICULTIES[taxonomy.difficulty_name(value)] != DIFFICULTIES[difficulty]:
                self.error("DIFFICULTY_CONFLICT", "difficulty 与 difficultyValue 档位冲突", uid)
        for key in ("region", "city", "district"):
            if key in filters and not isinstance(filters[key], str):
                self.error("FILTER_INVALID", f"filterMetadata.{key} 必须是字符串", uid)
        for key in ("subtypes", "categories", "semesters"):
            if key in filters and (not isinstance(filters[key], list) or any(not _nonempty(x) for x in filters[key])):
                self.error("FILTER_INVALID", f"filterMetadata.{key} 必须是字符串数组", uid)
        # Local taxonomy checks normalize whitespace for editing. Wire names must
        # already match the fixed catalog exactly; never trim them into validity.
        main_type = data.get("type", parent_type if child else None)
        if isinstance(main_type, str):
            main_type = TYPE_ALIASES.get(main_type, main_type)
        if not isinstance(main_type, str) or main_type not in taxonomy.types():
            self.error("TYPE_INVALID", "type 必须精确匹配固定题型目录名称", uid)
        allowed_subtypes = taxonomy.subtypes(main_type) if isinstance(main_type, str) else []
        subtypes = filters.get("subtypes", [])
        if isinstance(subtypes, list):
            for subtype in subtypes:
                if not isinstance(subtype, str) or subtype not in allowed_subtypes:
                    self.error("SUBTYPE_INVALID", "filterMetadata.subtypes 必须精确匹配对应题型的固定子类名称", uid)
        categories = filters.get("categories", [])
        if isinstance(categories, list):
            for category in categories:
                if not isinstance(category, str) or category not in taxonomy.categories():
                    self.error("CATEGORY_INVALID", "filterMetadata.categories 必须精确匹配固定分类名称", uid)
        if "options" in data:
            output["options"] = self.options(data["options"], uid)
        if "attachments" in data:
            output["attachments"] = self.attachments(data["attachments"], uid)
        if self.media_map:
            # 兼容尚未经过装配规范化的历史批次：按已部署媒体映射把 [IMG name]
            # 转成 attachments 并从正文移除标记；未映射的标记继续留在原处，
            # 由 scan_media 报错，绝不自动猜测 URL。
            from .wire_format import OPTION_PREFIX, replace_images
            attachments = list(output.get("attachments") or [])
            for key, placement in (("stem", "question"), ("material", "material")):
                if isinstance(output.get(key), str):
                    output[key] = replace_images(output[key], placement, None,
                                                 self.media_map, attachments)
            if isinstance(output.get("options"), list):
                normalized = []
                for option in output["options"]:
                    match = OPTION_PREFIX.match(option)
                    if match:
                        text = replace_images(option[match.end():], "option", match.group(1),
                                              self.media_map, attachments)
                        normalized.append(f"{match.group(1)}. {text}".rstrip())
                    else:
                        normalized.append(replace_images(option, "option", None,
                                                         self.media_map, attachments))
                output["options"] = normalized
            existing_count = len(output.get("attachments") or [])
            if len(attachments) > existing_count:
                attachments[existing_count:] = self.attachments(attachments[existing_count:], uid, remap=False)
            if attachments:
                output["attachments"] = attachments
        if not child and isinstance(main_type, str) and main_type.startswith("听力"):
            if not any(a.get("kind") == "audio" for a in output.get("attachments", [])):
                # 服务端上传暂存不强制音频；缺音频只记警告，允许先上传、后续补音频。
                self.warning("MISSING_AUDIO_ATTACHMENT",
                             "听力单元没有音频附件；按缺音频继续上传，可在题库后续补传", uid)
        for key in ("audio", "images", "image", "audio_map", "image_map"):
            if raw.get(key):
                self.error("MEDIA_DEPLOYMENT_REQUIRED", f"本地 {key} 不能自动生成公开 URL；请显式提供已部署 attachments", uid)
        if "materialBlocks" in data:
            self.blocks(data["materialBlocks"], uid)
            output["materialBlocks"] = _safe_blocks(data["materialBlocks"])
        if "relations" in data and (not isinstance(data["relations"], list) or any(not _nonempty(x) for x in data["relations"])):
            self.error("RELATIONS_INVALID", "relations 必须是关系名称字符串数组", uid)
        self.knowledge(data, output, uid,
                       allow_empty=bool(child and isinstance(parent_type, str)
                                        and taxonomy.whole_unit_type(parent_type)))
        self.mappings(data, output, uid)
        children = data.get("subquestions", [])
        if not isinstance(children, list):
            self.error("SUBQUESTIONS_INVALID", "subquestions 必须是对象数组", uid)
            children = []
        kind = data.get("unit_kind", "group" if children else "standalone")
        if kind not in ("group", "standalone"):
            self.error("UNIT_KIND_INVALID", "unit_kind 仅允许 standalone/group", uid)
        if child and (kind == "group" or children):
            self.error("NESTED_GROUP_INVALID", "子题不能再包含题组", uid)
            children = []
        if not child:
            output["unit_kind"] = kind
            if kind == "group":
                # Listening source papers normally print instructions/questions
                # while the shared dialogue exists in audio only. Do not invent
                # a written transcript to satisfy a reading-material rule.
                listening_group = (isinstance(main_type, str) and main_type.startswith('听力')
                                   and _nonempty(data.get('stem')))
                if not children or not (_nonempty(data.get("material")) or data.get("materialBlocks") or listening_group):
                    self.error("GROUP_INCOMPLETE", "题组必须有结构化子题及共享材料；听力题组可保留原卷听力说明", uid)
                expected_numbers = raw.get("numbers")
                child_numbers = [c.get("number") for c in children if isinstance(c, dict)]
                valid_numbers = (isinstance(expected_numbers, list) and bool(expected_numbers)
                    and all(_positive(n) for n in expected_numbers)
                    and len(expected_numbers) == len(set(expected_numbers))
                    and len(child_numbers) == len(children)
                    and all(_positive(n) for n in child_numbers)
                    and len(child_numbers) == len(set(child_numbers))
                    and set(child_numbers) == set(expected_numbers))
                if not valid_numbers:
                    self.error("GROUP_NUMBER_COVERAGE", "组级 numbers 必须与全部唯一的原卷子题号完整一致", uid)
                if deferred_group_answer:
                    if (valid_numbers and all(_nonempty(c.get("answer")) for c in children)):
                        output["answer"] = "\n".join(f"{c['number']}. {c['answer']}"
                                                     for c in sorted(children, key=lambda c: c["number"]))
                    else:
                        self.error("MISSING_ANSWER", "无法从完整、唯一题号且非空的子题答案构建组级答案", uid)
            elif children:
                self.error("UNIT_KIND_CONFLICT", "含子题的单元必须声明 group", uid)
        declared_options = output.get("options", [])
        if 0 < len(declared_options) < 2:
            # A single-option choice question is unambiguously broken; the
            # server would render an unanswerable item.
            self.error("OPTION_COUNT_TOO_SMALL", "选择题至少需要两个选项", uid)
        subtypes = filters.get("subtypes", [])
        numerals = {"二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}
        for subtype in subtypes if isinstance(subtypes, list) else []:
            if not isinstance(subtype, str):
                continue
            count = re.fullmatch(r"([二两三四五六])个选项", subtype)
            expected = numerals[count.group(1)] if count else None
            if expected is not None and len(declared_options) != expected:
                self.error("OPTION_COUNT_MISMATCH", f"子类{subtype}需要 {expected} 个完整选项", uid)
            count = re.fullmatch(r"选择(\d+)题|(\d+)空", subtype)
            if not child and kind == "group" and count:
                expected = int(count.group(1) or count.group(2))
                if len(children) != expected:
                    self.error("SUBQUESTION_COUNT_MISMATCH", f"子类{subtype}与 {len(children)} 个结构化子题不符", uid)
        if children:
            numbers = [c.get("number") for c in children if isinstance(c, dict) and _positive(c.get("number"))]
            if len(numbers) != len(set(numbers)):
                self.error("SUBQUESTION_NUMBER_DUPLICATE", "题组内子题号重复", uid)
            output["subquestions"] = [self.item(c, f"{uid}/{c.get('number', i) if isinstance(c, dict) else i}", True, main_type) for i, c in enumerate(children, 1)]
            # Aggregate names and IDs only on the wire, after the local audit.
            paths = output.get("knowledge", []) + [path for c in output["subquestions"] for path in c.get("knowledge", [])]
            output["knowledge"] = list(dict.fromkeys(paths))
            if self.version:
                ids = output.get("knowledge_ids", []) + [kid for c in output["subquestions"] for kid in c.get("knowledge_ids", [])]
                output["knowledge_ids"] = list(dict.fromkeys(ids))
            self.summary["questions"] += len(children)
        elif not child:
            self.summary["questions"] += 1
        # 空档统一成库侧写法：<u>　26　</u> → __26__，纯空档 → ______。
        # 装配阶段已归一化；这里兜底历史批次，避免把 <u> 原样写进题库。
        from .wire_format import normalize_blank_text
        for key in ("stem", "material"):
            if isinstance(output.get(key), str):
                output[key] = normalize_blank_text(output[key])
        blocks = output.get("materialBlocks")
        if isinstance(blocks, list):
            for block in blocks:
                for row in block.get("rows", []) if isinstance(block, dict) else []:
                    for cell in row.get("cells", []) if isinstance(row, dict) else []:
                        if isinstance(cell.get("text"), str):
                            cell["text"] = normalize_blank_text(cell["text"])
        self.scan_media({k: v for k, v in output.items() if k not in {"attachments", "subquestions"}}, uid)
        return output


def build_payload(batch: dict, catalog: dict | None = None, *, batch_id=None, name=None,
                  upload_mode=None, upload_target=None, media_map=None) -> dict:
    """Return validation evidence and an allowlisted staging batch; never mutate input.

    ``catalog_sha256`` binds canonical version/points JSON (full metadata), excluding
    transport metadata such as fetched_at. ``payload`` on failure is preview only.
    Known workflow controls are omitted; unknown extensions block readiness.
    """
    builder = _Builder(media_map)
    if not isinstance(batch, dict):
        builder.error("BATCH_INVALID", "批次必须是 JSON 对象")
        batch = {}
    builder.unknown(batch, {"items", "batch_id", "id", "name", "title", "subject_id", "upload_mode", "upload_target", "status", "warnings", "human_confirmed"}, "", "批次.")
    builder.catalog(catalog)
    builder.collect_media_sources(batch.get("items", []))
    items = batch.get("items", [])
    if not isinstance(items, list) or not items:
        builder.error("BATCH_ITEMS_INVALID", "batch.items 必须是非空数组")
        items = []
    # Audit the local labels before any remote ID replacement or group union.
    audit = label_audit.inspect_batch(_local_audit_view(batch))
    builder.errors.extend({"unit_id": "", **error} for error in audit["errors"])
    selected_id = batch_id if batch_id is not None else batch.get("batch_id", batch.get("id"))
    selected_name = name if name is not None else batch.get("name", batch.get("title"))
    mode = upload_mode if upload_mode is not None else batch.get("upload_mode", "manual")
    target = upload_target if upload_target is not None else batch.get("upload_target", "")
    if "batch_id" in batch and "id" in batch and batch["batch_id"] != batch["id"]:
        builder.error("ALIAS_CONFLICT", "根级 batch_id 与 id 不一致")
    if "name" in batch and "title" in batch and batch["name"] != batch["title"]:
        builder.error("ALIAS_CONFLICT", "根级 name 与 title 不一致")
    for field, value in (("batch_id", selected_id), ("name", selected_name)):
        if not _nonempty(value):
            builder.error("BATCH_" + field.upper() + "_REQUIRED", f"批次缺少显式 {field}")
    if mode not in ("manual", "source", "knowledge"):
        builder.error("UPLOAD_MODE_INVALID", "upload_mode 仅允许 manual/source/knowledge")
    if not isinstance(target, str):
        builder.error("UPLOAD_TARGET_INVALID", "upload_target 必须是字符串")
    elif mode == "knowledge" and not target.strip():
        builder.error("UPLOAD_TARGET_REQUIRED", "按知识点上传需要显式 upload_target")
    elif mode == "source":
        target = SOURCE_ALIASES.get(target, target)
        if target not in set(taxonomy.sources()) | {"精品题源", "课本原创"}:
            builder.error("UPLOAD_SOURCE_INVALID", "来源上传目标不在正式题源或业务别名中")
    payload = {"batch_id": selected_id, "name": selected_name, "upload_mode": mode, "upload_target": target, "items": []}
    remote_subject = catalog.get("version", {}).get("subject_id") if isinstance(catalog, dict) and isinstance(catalog.get("version"), dict) else None
    subject = batch.get("subject_id", remote_subject)
    if subject is not None:
        if not _nonempty(subject):
            builder.error("SUBJECT_INVALID", "subject_id 必须是非空字符串")
        elif remote_subject and subject != remote_subject:
            builder.error("SUBJECT_MISMATCH", "批次 subject_id 与远端目录版本科目不一致")
        else:
            payload["subject_id"] = subject
    seen = set()
    for index, item in enumerate(items):
        uid = str(item.get("id") or item.get("unit_id") or index) if isinstance(item, dict) else str(index)
        output = builder.item(item, uid)
        qid = output.get("id")
        if isinstance(qid, str):
            if qid in seen:
                builder.error("QUESTION_ID_DUPLICATE", f"重复题目 ID：{qid}", uid)
            seen.add(qid)
        if mode == "source" and output.get("source_type") != target:
            builder.error("SOURCE_TARGET_CONFLICT", "显式题目 source_type 与来源上传目标不一致", uid)
        payload["items"].append(output)
    builder.summary["units"] = len(items)
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 2_000_000:
            builder.error("PAYLOAD_TOO_LARGE", "上传请求超过 2,000,000 bytes，请拆分批次")
    except (TypeError, ValueError):
        builder.error("PAYLOAD_NOT_JSON", "上传数据必须是标准 JSON，不能包含媒体 bytes 或非有限数值")
        payload = None
    return {"passed": not builder.errors, "payload": payload, "errors": builder.errors,
            "warnings": builder.warnings, "summary": builder.summary, "catalog_sha256": builder.catalog_sha256}
