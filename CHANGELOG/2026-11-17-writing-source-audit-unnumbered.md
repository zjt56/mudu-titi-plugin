# 2026-11-17：书面表达无印刷题号不再触发来源窗口题号唯一性阻断

## 背景/触发

真实批次 `【木牍教育】2024中考英语试卷` 在 `validate` 阶段报：

```
原卷逐题覆盖与材料归属 source_audit：来源窗口内第 81 题号出现 0 次，无法唯一绑定
```

第 81 题是 `书面表达`，`source_span.number_binding=single_writing_section`。
`question_content.audit_source_unit` 的旧逻辑只豁免 `unnumbered_section`，仍按普通
小题要求“题号在窗口内恰好出现一次”，因此没有印刷题号的作文永远无法通过来源审计。

## 改了什么

- `app/workflow/question_content.py::audit_source_unit`：增加
  `single_writing_section` 豁免；普通小题和 `unnumbered_section` 的唯一性校验不变。
- 现有 `workflow.test_writing_answer_space` 6 项全部转为通过（修复前 3 项失败）。

## 没改什么 / 边界

- 只豁免“作文没有印刷题号”这一合法形态，不豁免作文题干/开头句/范文的来源核对；
  真实题干改写、写作内容缺失仍按既有规则处理。
- 不改变上传题号投影：作文仍由 `single_writing_section` 投影为 `null/null`。

## 如何验证

- `workflow.test_writing_answer_space`：6 项通过。
- `workflow.test_agent_tasks`、`test_early_defer_policy`、`test_format_gate`、
  `test_structure_gate`、`test_content_structure`、`test_question_content`：162 项通过（4 跳过）。
- 真实批次 `【木牍教育】2024中考英语试卷`：`validate` 13 项检查全通过，`passed=true`。

## 回退方式

删除 `audit_source_unit` 的 `writing` 豁免条件即可恢复旧行为（会让无号作文重新被来源审计阻断）。
