# 短文填空图注空识别 + 视觉栏目归属健壮性

日期：2026-11-13。类型：运行行为 + 校验器容错 + 视觉证据解析健壮性。

## 背景与触发

批次「11七上人教版课时A计划StarterUnit3Welcome!第三课时Sect」答案填充阶段失败：
`sec-03770200d1ca1625-u47/题3: scoped answer source is not uniquely bound`。

根因：短文填空（Ⅳ，正文第 47 行）实际有 8 个空：`1.[IMG image10.jpeg](tree)`、
`2.[IMG image10.jpeg](/aːnt/)` 是「图片+紧随括号提示」的**图注空**，`3.<u>…</u>`…`8.<u>…</u>`
是文本空。锚点识别（`_weak_numbers` / `blank_numbers`）只认下划线/全角空格空位，不认
`N.[IMG …](hint)` 图注空，故结构只收 3-8；而答案附录 Ⅳ 键是 `1.trees 2.aunt 3.So…8.beautiful`
（1-8），`_compact_scoped_candidates` 的完整性检查 `printed=[3..8]` vs `keyed=[1..8]` 不符，绑定失败。
（视觉整页栏目归属模型也独立判定该大题 `printed_numbers:[1..8]`，佐证 8 个空存在。）

修复过程中，因为重跑 extract 触发视觉整页栏目归属链路，又暴露三个健壮性问题：
参考答答案页被模型当作 `issues` 上报被判失败、模型 response 含 tab 控制字符 `json.loads` 严格模式失败、
模型把 `printed_numbers` 写成数字字符串、以及 4 页视觉调用偶发 abort/超时/截断。

## 改了什么

- `app/workflow/extract.py`：新增 `IMAGE_HINT_BLANK`（`N.[IMG …](提示)`/`（提示）` 图注空正则）。
  `blank_numbers` 在保留「空位在前」识别的基础上纳入图注空；**题号在前**（`3.<u></u>`）只在同步练习
  组题类型下由 `infer_structure` 里 `blank_numbers ∪ _weak_numbers` 合并收集，避免非同步卷把材料行
  普通数字误当题号（回归：`test_non_sync_exam_does_not_gain_dialogue_inline_fallback`）。
- `app/workflow/preview.py`：`_weak_numbers` 增加同源 `_IMAGE_HINT_BLANK`（图片+括号提示才计图注空；
  孤立 `N.[IMG …]` 仍不计）。
- `app/workflow/visual_columns.py`：
  - `_columns`：`issues` 只把**非良性**项判失败 —— 参考答答案区「未建题」说明属合规良性（提示词本就
    要求不把参考答案区作为新题），真未覆盖/不确定仍失败（`_benign_issue`）。
  - `_quote_eq`：源码引文比对只容忍首尾空白/NBSP（Word 行尾 `\xa0`），内部仍逐字一致；`json.loads`
    用 `strict=False` 容忍字符串内 tab 等控制字符（源文本本身含 `\t`）。
  - `printed_numbers` 接受纯数字字符串并归一化为 int，非数字/重复仍拒绝。
  - 视觉调用：可配 `WF_VISUAL_TIMEOUT`（默认仍 180s）、`WF_VISUAL_RETRIES`（默认 3）；对
     abort/超时/截断做同路由有界重试（截断时递增 max_tokens），绝不换模型或 OCR 兜底。
  - 复用：同一输入/路由已捕获的 `response_text` 在当前规则下合法时直接写 verified，不再重复烧多页调用
    （放在 300s 冷却之前，因复用确定且不弱于新调用）。
- `app/workflow/structure_gate.py`：`_proven_scoped_restart` 透传 `visual_columns` 给
  `_scope_windows` 与 `assign_scoped`。此前重推 `source_section` 不带列归属，导致带 `column_path`
  的单元与重推结果不一致，把所有 `NUMBERING_RESTART` 误判失败。
- `app/workflow/question_identity.py::assign_scoped`：**有印刷标题的大题**即使匹配了视觉栏目，
  也以标题派生身份（真实课时上下文 + 全局 occurrence）为准，栏目只作附加元数据；仅**无标题**的
  视觉实际题才走 `visual-column:<id>` 伪上下文 + occurrence 1 的纯视觉归属。此前所有匹配栏目的
  大题 context 被写成 `visual-column:<id>`、occurrence 重置为 1，而答案绑定经 `heading_candidates`
  重推的是真实上下文 + 全局序次，两者不一致导致 u47/u50/u75 全部 `scoped answer source is not
  uniquely bound`。修复后结构身份与原文重推一致，36 个子题答案全部唯一绑定。

## 没改什么 / 边界

- 仍是同一来源唯一绑定：不猜、不借全局题号、不按题号合并；`printed_numbers` 归一化只针对纯数字串。
- 视觉调用仍走当前选定路由，失败只重试同路由、超时/重试预算可配，不回退其它模型/OCR，不伪造已看整页。
- 良性 issue 仅限参考答答案区未建题；真实未覆盖/不确定/伪造坐标/内部引文改动/重叠/非法 bbox 仍拒绝。
- 结构闸门、答案绑定、上传边界不变；不改用户历史批次，验证用本批次的既有原件与只读证据。

## 验证

- `cd app && PYTHONPATH=. python3 -m unittest workflow.test_extract_sync_dialogue_blanks workflow.test_visual_columns
  workflow.test_upload_content workflow.test_upload_content_equivalent_anchors workflow.test_answer_scope_trailing_lines
  workflow.test_answer_scope_blank_evidence workflow.test_roman_answer_appendix workflow.test_scoped_lesson_answers
  workflow.test_scoped_lesson_mixed_answers workflow.test_structure_gate workflow.test_preview_labels
  workflow.test_question_identity_preview workflow.test_question_identity_answers workflow.test_content_structure`
  → 218 tests OK（回归验证子智能体复跑）。
- `infer_structure` 对批次正文：Ⅳ短文填空 = [1,2,3,4,5,6,7,8]，其余大题不变（Ⅰ5/Ⅱ6/Ⅲ5/完形10/阅读4）。
- 结构闸门：`structure_gate` 通过；`structure.confirmed` u47 numbers=[1..8]。
- 全链路复验（本批次）：extract → structure_gate → preassemble → types → answers → assemble →
  meta → underlines → validate 全部通过；`validate` 本地体检 14 项 0 阻断失败。
- 答案绑定：u47 短文填空 1-8 全部唯一绑定（trees/aunt/So/at/old/kinds/gives/beautiful）；
  u50 完形 1-10、u75 阅读理解 1-4（含 ☆拓展设问 "Many of them…"）均绑定；answers 阶段确定性直抄、
  model_calls=0。
- 单测：受影响套件共 264 tests OK（含 test_extract_sync_dialogue_blanks / test_upload_content /
  test_scoped_lesson_answers / test_scoped_lesson_mixed_answers / test_structure_gate /
  test_question_identity_* / test_visual_columns / test_content_structure / test_structure_visual_binding /
  test_audit_type_layout / test_answer_sole_topic 等）。
- 已知非本改动：`workflow.test_answer_lesson_bands::test_real_verified_visual_artifact_controls_direct_and_validator_consumers`
  在本次改动前的 HEAD(19a22fc) 上即失败（视觉板块证据相关，非本次范围）。

## 回退方式

回退上述四个文件到本提交前即可；图注空/视觉容错均为增量识别，不改变旧批次既有判定口径。
