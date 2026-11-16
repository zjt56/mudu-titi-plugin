# 课时答案区被判畸形、表格图片小题漏号、批次身份不随结构刷新

日期：2026-11-16
批次：`10七上人教版课时A计划StarterUnit3Welcome!第二课时Pron`

## 背景/触发

用户报告卡点（原话要点）：该批次「答案填充：阶段失败」，四个单元
`sec-db3bc6e0772e2dd9-u7`、`sec-256148bc4b3fb96e-u24`、`sec-1086f8e7b7436938-u31`、
`sec-4678978cdafbe271-u41` 全部报
`scoped answer source scope unbound: duplicate or malformed compact answer block`，
要求按具体失败单元定位输入/输出/校验器问题、自动修正后用原检查复验、保留已验证单元、不原样重试。

读源发现三处确定性缺陷叠加，任何一处都会让整批四单元一起失去答案来源：

1. 附录原文 `Ⅲ.1~5 DSDDS` 是判断题的 S/D 答案。紧凑答案块的字母类写成
   `[A-HFTa-hft]+`，S 不在其中 ⇒ 该块解析为空 ⇒ 被标 `invalid`；
   `_compact_scoped_candidates` 对任一无效块直接 `raise`，于是同板块的四个单元全部判失败。
2. Ⅱ 歌谣填表的第 6 空原文是 `6.[IMG image40.jpeg]`（只有一张小图，答案给 `6.sit`）。
   `preview` 只把编号空档和「图号+括号提示」当小题，表格行里与编号空档相邻的图片号被漏掉
   ⇒ 确认题号只有 1–5、7–9，答案 6 无题可绑（真实漏题）。
3. 批次填充只刷新正文与 `source_span`，结构重切后的 `source_section`（含 `column_path`）、
   `question_ids`、`printed_numbers` 没进入已标注行。语音组题的 A8/A9 来源证明按完整身份比对，
   旧身份让 u31 被判「题组 material 为空」，组装前置校验失败。

另在复跑知识点阶段时遇到内容代理一次工具参数少一个右括号（`content_submit_candidate`
参数 JSON 在第 2779 字符提前结束）就整阶段失败；会话记录证明其余候选内容完整，
属于格式失误被当成终局失败。

## 改了什么

- `app/workflow/upload_content.py`
  - `_COMPACT_RANGE` / `_ANSWER_TOKEN` 的字母组放宽为 `([A-Za-z]+)`；仍要求
    字母数 == 区间长度、编号唯一，词答案（如 `1~5 cats`）继续拒绝。判断题
    `1~5 DSDDS`、`1~3 TFT` 现在能解析。
  - 板块窗口守卫改为 `_shortened_band_window(owner_span, annotated)`：只拒绝
    「起点就是板块横幅、却提前结束且后面还有同板块大题」的窗口；Ⅰ/Ⅱ 这类位于板块内部的
    单个大题窗口是合法子窗口，不再被误判为「窗口被截断」。
- `app/workflow/preview.py`：新增 `_PICTURE_CELL`、`_printed_blank_numbers`、
  `_picture_cells`、`picture_cell_numbers`；`_weak_numbers` = 编号空档 + 与空档序列相邻的
  图片小题。孤立 `6.[IMG …]`、带选项前缀的 `A.6.[IMG …]` 仍不算小题。
- `app/workflow/question_draft.py`：图片小题按新锚点进入子题；题干留 `______` 空位
  （图片仍留在材料/单元附件里），不再报 `CHILD_CONTENT_UNRESOLVED` 漏题。
- `app/workflow/batch_fill.py`：新增 `IDENTITY` 与 `_sync_identity`；刷新行时把
  `identity_version/section_id/source_section/source_span/question_ids/printed_numbers`
  与子题 `question_id` 同步到已标注行，同时保留已产出标签与已核对答案；`initialize`
  的来源未变分支同样走这一同步。
- `app/workflow/phonetic_structure.py`：新增判断题形态（题干明确写
  「判断/正误/是否相同/是否一致」且给出 S/D 或 T/F 字母对，2–4 个印刷选项、空题干、无 material）
  作为合法语音组证明；4 选项 A–D 的 `choices_only` 路径与「选项缺失仍拒绝」保持原样。
- `lib/content-agent.js`：识别「工具参数不是合法 JSON」类 turn 错误，保留会话并按
  明确格式修复提示重提一次，上限 2 次（`FORMAT_RECOVERIES`），仍失败才按原错误终止。
- 回归测试：`test_extract_sync_dialogue_blanks`（表格图片小题）、
  `test_scoped_lesson_mixed_answers`（S/D 紧凑答案）、`test_answer_lesson_bands`
  （内部大题窗口）、`test_question_draft`（图片小题空位题干）、`test_batch_fill`
  （身份同步保留标签）、`test_phonetic_structure`（判断题 3 选项形态 + 反例）、
  `backend/content-agent.test.cjs`（一次格式失误后同会话成功 / 连续失误耗尽预算）。

## 没改什么 / 边界

- 不重编号、不放宽答案与原文的逐字绑定；判断题仍然要求「题干写明判断 + 字母对」，
  普通 3 选项题没有该证据就照旧要求 material。
- 审核差异不自动消解：本次复跑后 u41 的知识点标签（原标注多一条「推理判断」）进入
  `review_gate=needs_review`，保留在人工队列，属设计状态，不算卡点、不阻断用户手动上传。
- 不自动上传/发布；`upload_gate/upload/publish_gate` 仍为人工/未执行。
- 不改历史批次结构；本次只修复该批次并重新生成其自身派生产物（动手前已整批备份）。

## 验证

- 定向回归：`python -m unittest workflow.test_extract_sync_dialogue_blanks
  workflow.test_scoped_lesson_mixed_answers workflow.test_answer_lesson_bands
  workflow.test_question_draft workflow.test_batch_fill workflow.test_phonetic_structure`
  → Ran 141 tests, OK（含新增用例）。
- 相关面回归：extract/structure_gate/preview/答案作用域/组装/上传编号等 18 个模块
  → Ran 356 tests, OK（33.386s）。
- 宿主与前端：`node --test backend/*.test.cjs` → 376 tests，357 pass / 18 fail / 1 skip；
  18 个失败与改动前基线日志（`md-upload-tiku-head-ocr-baseline-backend-tests.utf8.log`）
  同名同数量，无新增失败，`content-agent.test.cjs` 49 项全通过。
- 批次真实复验：结构重切后 u24 = 1–9（含图片小题 6），答案 22 条全部绑定
  （`01_source/附录.txt:3/4/5/6/8`，含 `6.sit` @ `01_source/附录.txt:4`）；assemble → meta →
  underlines → validate → review → format_gate 全部完成；`07_review/validation-report.json`
  `passed=true`；答案阶段整批校验不再报 scope unbound。

## 回退

`git revert` 本提交即可；批次侧可用修复前备份
`%TEMP%\ocr-fix-backup-20261116\10StarterUnit3Welcome-2`（119 个文件）整体还原。
