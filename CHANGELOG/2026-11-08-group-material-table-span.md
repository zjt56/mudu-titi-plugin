# 2026-11-08 修复表格型阅读题组 material 丢失（A8）

## 背景

2024 卷 group-56-57-58（阅读理解）装配报 A8「题组 unit_kind=group 但 material 为空」。该组材料是 `[TABLE ...]` 表格（正文 153–169 行），但 `question_draft` 生成时 material 为空，导致装配整批校验失败。

## 根因

`question_draft._bound_source` 只对 `source_span.basis == "ai_classified"` 走整窗边界；本卷该组 basis 是 `cleaned_source`，于是退回“按 stem 唯一匹配定位”。stem 只含题干（第 56 题起），定位结果从第 160 行开始，把前面的表格材料（153–159 行）切掉，`build()` 的 group 分支据此得到空 material。

## 改了什么

- `app/workflow/question_draft.py::_bound_source`：当 `unit_kind == "group"` 且 `source_span.basis ∈ {ai_classified, cleaned_source}` 且行号合法、stem 能在窗口内定位时，返回**整个 confirmed span 窗口**（材料+题目）作为绑定文本，而非只框住 stem。定位仅用于证明 stem 在该窗内，不再缩短材料。
- 仅作用于 group；standalone 仍只绑定自身题干，行为不变。

## 验证

- 2024 卷 group-56-57-58：`question_draft.build` 生成 material 长度 736（含完整表格材料）、subquestions 3 个，每子题 4 选项。
- 重跑 `assemble`：`{"deterministic": true, "model_calls": 0}` → completed；`publish_check` 不再报 A8。
- `workflow.test_question_draft` + `test_source_structure` + `test_assemble_precheck` + `test_question_content`：50 项通过（4 跳过）。

## 边界

- 只修 group 材料绑定，不改题目内容、答案、标签；standalone 与其它卷不受影响。
- 未改用户已完成的批次；仅重跑 2024 卷 assemble 使产物与修复一致。
