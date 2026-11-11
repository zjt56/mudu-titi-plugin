# 2026-11-12 修正图片选项题 A/B/C/D 选项图错位（dict 选项按 key 绑图）

## 1. 背景 / 触发

用户反馈 04 批次「拓展设问」第4题（图片理解）A/B/C/D 的选项图有一个放错位置。核查：
现有 batch.json 该题 attachments 为 None→image14、A→image15、B→image16、C→image17，
D 无图——每个图整体错位一格、首图未绑任何选项。根因链：

1. `question_content.question_parts` 对「图在选项标签前」排版 `[IMG x]A.` 按“标签后到
   下一标签”切，A 拿到下一张图、末项为空；
2. 组装 `question_draft._parts` 沿用该错位；
3. `wire_format.normalize_item` 处理 options 时假设是字符串（`OPTION_PREFIX.match`），
   但图片前置选项题产出的是结构化 dict `{key,text:[IMG]}`, 被当文本、optionKey=None →
   图全错位落到 None。

## 2. 改了什么

- `question_draft._image_prefixed_options(raw)`：解析 `[IMG x]A. … [IMG y]B.` 前置图片选项
  排版，按原文顺序把第 k 张图配到第 k 个标签（仅当 ≥2 个 `[IMG]X.` 且标签从 A 连续时生效，
  否则返回 None 走原逻辑，不影响普通文本/`A.[IMG]` 后置形态）。`_parts` 命中即用。
- `wire_format.normalize_item`：options 支持 dict `{key,text}`——用 key 作 optionKey 绑定
  图片、输出为字符串标签（保持既有字符串形态，普通 dict 无图仍 `A. go`）。
- 回归：`test_question_draft.ImagePrefixedOptionsTest`(2)、
  `test_wire_format.test_structured_option_dict_binds_image_by_key`(1)。

## 3. 没改什么 / 边界

- 不改 `question_content.question_parts` 全局切割（避免影响 legacy/self_heal/audit 多调用方）。
- 不改原文、不生成内容；只修正选项与图片的绑定关系。
- 仅对“图片前置选项”形态生效，纯文本选项、`A.[IMG]` 后置、完形等不受影响。

## 4. 如何验证

- 真机 `question_draft.ensure(04)`：第4题 attachments 为 A→image14、B→image15、C→image16、
  D→image17（正确，无 None）。
- `wire_format` 单测：图片 dict 选项→attachments 按 key 正确绑、options 输出 A./B./C./D.；
  普通 dict 选项仍 A. go/B. see。
- `test_wire_format+test_question_draft+test_assemble_local+test_assemble_precheck+
  test_structure_gate+test_question_content+test_legacy_format` 107 例全过。
- 04 批次：invalidate preassemble 下游→重建 question-draft(正确)→重跑 preassemble→assemble
  后 batch 选项图应正确到位。

## 5. 回退方式

git revert 本提交即可；无数据破坏，重跑可再生成。
