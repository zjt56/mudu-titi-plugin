# 2026-11-11 画线阶段：整行表格标签不再被当“孤立字段”拒收

## 背景（触发）

12 年安徽卷（`C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】12年安徽中考英语`）
解析阶段改用确定性直抄跑完后（`04_knowledge/explanations.json` 52 单元，`model_calls=0`），
`assemble`/`meta` 通过，`underlines` 整阶段失败：

```json
{"passed": false, "changes": [], "issues": [
  {"source_context": "TestingDates:", "underlined": "TestingDates:", "code": "UNDERLINE_MAPPING_MISSING",
   "message": "完整原文上下文对应 0 个位置，未自动改动"},
  {"source_context": "ParentsMeeting:", ...}, {"source_context": "Homework:", ...}]}
```

原卷这三处都是**整段只有一条画线的表格标签**（`01_source/正文.txt:252/255/257` 为
`<u>Testing Dates:</u>`、`<u>Parents Meeting:</u>`、`<u>Homework:</u>`），但组装后的
`batch_meta.base.json`（`group-86-87-88-89-90`）把整张表格压成了一串 material，
标签于是嵌在很长的正文里。

## 根因

`app/scripts/06-upload/mark_underline.py::restore` 里有一条保护分支：画线文本若与
字段整体相等，则只允许它**独占**该字段/选项，否则跳过（防止短词在长正文里乱匹配）。
整行标签天然满足「上下文 == 画线文本」，于是无论字段多长都命中这条 `continue`，
最终报 0 个位置。此前同类问题只作提示，这次因为三处同时缺失，整阶段被判失败。

## 改了什么

`app/scripts/06-upload/mark_underline.py`：该分支改为区分「整行标签」与「含答题线的标签」：

```python
unlabelled = re.sub(r"^[A-H][.．、]", "", value)
if unlabelled != plain_context and ("__" in marked_value or len(marked_value) < 3):
    continue
```

- 纯文字整行标签（≥3 字符，无答题线）允许在合并段落里定位，落位仍须通过后面的
  唯一性校验（`len(matches) > 1` → `UNDERLINE_MAPPING_AMBIGUOUS`）与同单元镜像校验；
- 含答题线的标签（`<u>Read __</u>`）或单字标签继续拒绝嵌入匹配，避免把答案线
  匹配到任意正文空格上；
- 字段整体恰好等于标签时（如 `Read ______`）照旧放行。

## 没改什么 / 边界

- 不放宽任何唯一性/镜像判定：多处命中仍报歧义并保持候选不变。
- 不改 DOCX 原卷，不改题目文本，只恢复画线标记。
- 不代表画线布局已在视觉上人工确认（`visual_review_claimed=false`）。

## 如何验证

- 回归：`workflow.test_underline_layout + workflow.test_underline_glyph_gate` → **21 项用例全部通过**；
  新增 `test_whole_line_label_inside_merged_passage_is_restored`（合并 material 里的
  `Testing Dates:`/`Homework:` 必须被包上 `<u>`）与
  `test_whole_line_label_must_stay_unique_across_units`（两单元同名 → 歧义）；
  既有 `test_bare_marked_field_with_rule_cannot_match_embedded_prose`、
  `test_rule_at_end_of_mark_preserves_its_entire_width` 保持通过。
- 受影响模块整体：15 个模块 **262 项用例全部通过（38.8s）**。
- 真实批次：`workflow.runner run --stage underlines` → `{"passed": true, "issues": []}`；
  `06_batch/batch_meta.json` 中三处标签各出现 1 次 `<u>`；随后 `validate` 19 项体检
  **阻断 0、提示 0**，`review`/`format_gate` 依次完成，最终 `review_gate=needs_review`
  （17 项差异待人工判断），`upload_gate/upload/publish_gate` 仍 pending。

## 回退

回退 `mark_underline.py` 的该分支到「整体相等即跳过」，画线阶段会重新报
`UNDERLINE_MAPPING_MISSING`（数据不会被改坏，但该批次会停住）。
