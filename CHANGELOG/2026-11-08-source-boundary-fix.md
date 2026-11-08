# 2026-11-08 修复来源结构边界选项审计

## 背景

`workflow.test_source_structure` 中多个回归用例失败，表现为完形填空、补全对话和 DOCX 换行场景的源头选项被判为空或不完整。根因不是题型规则，而是确认结构将选项记录在 `source_parts`，正式 `stem` 只保留题干/材料；审计只读取 `stem`，因此看不到题号后的选项。

## 改动

- `app/workflow/question_content.py`：`audit_source_unit` 在传入原文行时，按已确认 `source_span` 读取原始清理文本进行来源选项审计；不改写确认结构，也不把选项拼进正式 `stem`。
- `app/workflow/structure_gate.py`：结构闸门调用来源审计时传入当前原文行。
- `app/workflow/question_content.py`：正式批次最终审计仍保持原有 confirmed stem 语义，只在结构闸门检查来源选项，避免把章节标题或原始排版错误带入材料匹配。
- 来源材料审计忽略章节标题行：章节标题是结构元数据，不是题目材料，补全对话等场景不再要求候选材料重复章节标题。
- `test_source_structure` 的合成候选按 `source_span` 读取选项，测试与正式结构契约一致。

## 边界

- 不改变题型、题号、分组、正式 `stem`、`options`、审核口径或上传规则。
- 不放宽选项内容校验：A-D 标签、数量和非空内容仍需与原文一致。
- 不改变用户批次；没有直接改写任何 `workflows/` 数据。

## 验证

- `workflow.test_source_structure`：18 项通过；完整运行约 541 秒。
- `workflow.test_question_content`、`test_assemble_precheck`、`test_assemble_local`、`test_agent_tasks`：69 项通过，4 项跳过。
- `git diff --check` 与 Python 编译检查通过。
