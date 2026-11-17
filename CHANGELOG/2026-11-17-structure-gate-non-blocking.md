# 结构确认只产出结构化字段，不做阻断校验

## 背景

用户 2026-11-17 明确：结构确认这一环的主要工作是**把试卷拆成结构化题目并填入后续所需字段**，不做阻断性校验；验证环节放到后面的格式检查。追问后确认三点：

- 结构确认**完全不阻断**；
- 拆不清的疑点**记为 warning/留痕，继续下游**；
- 只改这一环，后面的体检/格式闸门不动。

此前结构闸门把题号覆盖、材料归属、题干保真、题号重号、组题越界、来源冲突等全部作为 `blockers`，任一不满足就把阶段标 `BLOCKED`，流程卡在结构确认。

## 改了什么

- `app/workflow/structure_gate.py`：
  - 正文为空时同时清空 `sections`，确保不产出空壳单元；
  - 所有疑点统一降级：写入新的 `structure_warnings` 列表，并以 `blocking=false`、`status=structure_warning` 并入 `warnings`；
  - `report["blockers"]` 恒为空；`passed = bool(units)` —— 只有拆不出任何单元才算无法交付。
- `app/workflow/runner.py::do_structure_gate`：结构闸门不再因疑点标 `BLOCKED`；`unit_counts.total` 为 0 时才 `BLOCKED`，否则记录完成（`notes` 写明“不阻断校验，疑点留痕”+ 具体告警码/行号）。
- `app/workflow/contracts.py`：`structure_gate` 阶段描述改为“只产出结构化字段、不做阻断校验、验证集中在最终格式闸门”。
- `app/docs/structure.md`：新增「本阶段不做阻断校验」小节，说明 `structure_warnings` 与唯一例外。
- 测试按新语义更新（原断言“必须阻断”的用例改为断言“仅留痕、不阻断、仍产出单元”）。

## 没改什么 / 边界

- 只改结构确认这一环；`validate`、`format_gate` 及来源/答案/内容对照保持不变。
- 结构闸门仍会**如实计算**全部疑点（题号覆盖、材料归属、保真、重号、组题、来源冲突等），只是不再据此阻断，全部落 `structure_warnings` 留痕，便于后续定位。
- 没有放宽任何**下游**硬校验：最终格式闸门、上传 `build_payload` 的身份/题号/来源校验照旧。
- 不修改历史批次数据，不造完成态。

## 验证

- 直接相关：`test_structure_gate`(19)、`test_structure_source_shape_regressions`、`test_structure_ai`、`test_ocr_structure_gate`、`test_source_answer_only`、`test_metadata_placeholders` 全部通过。
- 分块全量与“视觉停用”提交（`32aa4e3`）对比：**新增失败 0**；失败数 70→68（其中 changelog 文档格式问题在本轮顺带修好，另一个是偶发用例单跑通过）。
- `test_changelog_policy` 通过。

## 回退

`git revert` 本提交即恢复“疑点阻断”行为。

## 未决点

- 结构疑点不再拦截，意味着明显拆错的批次也会继续流到下游；是否要在**格式闸门**增加一轮“结构疑点汇总提示”由后续按需决定，本轮未做。
