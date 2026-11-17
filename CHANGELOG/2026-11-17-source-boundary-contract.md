# 2026-11-17：统一题目来源边界与旧协议歧义阻断

## 背景

批次 35 暴露出题目解析把后续题目的图片选项绑定到当前题。进一步审计发现，旧协议重复题号还可能通过字典覆盖，使不同大题互相复用题干、选项或图片。

## 改了什么

- `app/workflow/question_content.py` 统一 `audit_question`、`question_parts` 与来源审计使用 `_question_window`，不再让结构审计使用另一套 `preview.slice_text` 边界。
- 缺失题号时，`question_draft._parts` 只检查单行弱锚点，不再扫描到单元末尾。
- 旧协议来源索引遇到重复题号时显式标记歧义并阻断，不再由后一个单元覆盖前一个单元。
- 旧协议单元窗口内同一题号出现零次或多次时报告 `SOURCE_NUMBER_AMBIGUOUS`。
- `audit_batch_source` 将清洗后的正文行传入 `audit_source_unit`，同时校验确认的来源行范围和偏移。
- 新增跨题图片选项、缺失题号、重复旧题号和重复来源键回归测试。

## 边界

本次只收紧来源绑定与确定性审计，不自动改写用户答案、解析、知识点或审核结论；真正的审核差异仍保留给人工判断。

## 验证

- `workflow.test_question_content workflow.test_question_draft workflow.test_batch_fill workflow.test_review_evidence_scope`：111 项，107 通过，4 跳过，0 失败。
- 结构/提取扩展套件仍有一个既有 `SOURCE_COORDINATE_MISMATCH` 测试错误（`denoise.py` 中与本次无关的并行修改），未将其计入本次通过。

## 回退

回退本次提交即可恢复之前的来源索引和审计边界；不触碰用户批次历史数据。
