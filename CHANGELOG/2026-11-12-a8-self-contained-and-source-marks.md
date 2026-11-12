# 2026-11-12 A8 自包含组题豁免 + 组题原卷标记补入 stem + Symbol 字体误报

## 1. 背景 / 触发

05 批次（七上人教版 Starter Unit 2 第一课时 Section A）卡点：
`[A8] sec-70effc938c7cd4fd-u7 (单词拼写): 题组 unit_kind=group 但 material 为空`。

排查（**用视觉核对原卷页图** qlmanage 渲染 docx → 看图）：该题是**四线三格写单词**，
三条诗句下各画一个空白三格网格，学生填词用——**原卷本来就是空白作答网格**，提取出的
`[TABLE 1/2/3]`（3 行全空）是忠实还原。按硬规则 9（空白作答网格合法）与硬规则 25
（四线三格→group），此组题 `group` 正确、`material` 为空也合法，**A8 属误报**。

继续体检又暴露两个真问题：
- `audit_source_marks`：`[跨学科]`、`[2025·滁州天长市月考]` 只出现在 `source_section`
  元数据，未进入题干/材料——硬规则 25 明确「仅 source_section 含标记不算交付」。
- `underlines`：`DOCX_EXTRACTION_UNCERTAIN: Symbol font`，实际该 docx 的「符号字体」
  只是 `Segoe UI Symbol` 承载 12 个 `★`(U+2605) 装饰星号，不可能改写字面。

## 2. 改了什么

1. `question_content.self_contained_group(unit)`（新增，统一口径）：组题**每个**子题都有
   非空 stem 且每个子题 stem 自带 `[TABLE`/`[IMG`（四线三格空白网格、看图题等"每小题目
   包含内容"形态）→ 组级无需共享 material。阅读理解/完形/任务型等共享语篇不满足，仍报 A8。
2. `scripts/06-upload/check_structure.py` 与 `scripts/06-upload/publish_check.py`：
   A8 判定接入上述同一函数（两处共用，不各写一套）。
3. `question_draft.build()`：组题无导语时，若本节标题行携带原卷提示标记，则把该标题行
   原样补入 `row["stem"]`（大题作答指令），修复标记只存在于元数据的问题（硬规则 25）。
4. `docx_extract.py`：Symbol 字体告警从 `formatting()`（无文本上下文）移到 `inline()`，
   仅当 run 文本含 **ASCII 字母/数字**（Wingdings 类会把字母改写成图符）或**私用区码位**
   （需字体映射）时告警；`★` 等已明确 Unicode 符号不再误报、不再阻断画线。

## 3. 没改什么 / 边界

- 未放宽 A8 的实体要求：阅读理解/完形等共享语篇组题 material 为空仍阻断（有测试钉住）。
- 四线三格若某个子题缺网格（不完整自包含）仍报 A8。
- Wingdings + ASCII/私用区仍告警（既有测试通过），只去掉"明确 Unicode 符号"的误报。
- 不改原文、不生成内容；标题行本身就是原文唯一一行，原样补入。

## 4. 如何验证

- 视觉核对：qlmanage 渲染 05 docx 页图，确认三条诗句下均为**空白**四线三格。
- 真机：`check_structure` 与 `publish_check` 对 05 均通过（24 单元）。
- 真机：05 重新生成 question-draft 后，u7 stem=「Ⅰ.[跨学科]在下列四线三格中写出…」、
  u121 stem=「Ⅲ.阅读理解[2025·滁州天长市月考]」，标记进入题干。
- 真机：05 docx 重新提取 `warnings: []`（原为 Symbol font），正文 4545 字不变。
- 测试：`test_docx_extract`(13) + `test_native_unnumbered_structure`(含新增
  A8SelfContainedGroupTest 4 例) + `test_question_draft` + `test_wire_format` +
  `test_legacy_format` 共 83 例全过。

## 5. 未完成 / 阻塞

05 批次**未能跑完**：模型额度用尽（WorkBuddy 429 insufficient_quota，账户级），
preassemble/answers/knowledge 等模型阶段无法执行；按规则额度属瞬时限制、不算卡点。
额度恢复后执行：
`python -m workflow.runner run --batch-dir workflows/05… --until validate`

## 6. 回退方式

git revert 本提交；A8 与 stem 均为纯判定/组装改动，无数据破坏。
