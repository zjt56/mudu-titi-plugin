# 2026-11-17 视觉提供方失败改为可续查等待

## 背景 / 触发

真实批次 `【木牍教育】2024中考英语试卷` 的 16 页视觉证据已准备，但视觉提供方连续返回 `PROVIDER_CALL_FAILED`。此前结构闸门把这类外部暂时故障与题号、来源、栏目归属错误一起标为永久 `blocked`，调度无法区分“等待外部恢复”和“需要修复原卷结构”。

## 改了什么

- `visual_columns.build()` 统一识别提供方暂时故障，保留有界重试，并在 `visual-columns.json` 写入 `deferred=true`、`retryable=true`；仍写 `status=unavailable`、空 `columns`，因此不会产生任何视觉证据。
- `structure_gate.run_gate()` 将该外部等待写入 `deferred`，同时保留 `COLUMN_OWNERSHIP_UNRESOLVED` 阻断和 `passed=false`。真实题号重复、数量不符、来源歧义、作文题号推断和栏目归属不确定不进入延期豁免。
- `runner.do_structure_gate()` 对“只有视觉提供方暂时不可用”的报告把阶段置为 `pending`、退出码 `6` 并留下“已延后”原因；存在任何硬结构阻断时仍为 `blocked`、退出码 `1`。手动审核队列不再为外部等待生成伪差异。
- `auto` 调度把退出码 `6` 归为 `waiting_external`，可在后续轮次重试；结构闸门未通过时，依赖它的下游仍不会执行。

## 没改什么 / 边界

未改变视觉证据的 hash/来源/页覆盖校验，未伪造 `columns`，未修改 `structure.confirmed.json`、题号、用户批次或上传边界。坐标端点、阅读题数量、重复/歧义题号等实质错误仍必须修复并复验。

## 如何验证

- `PYTHONUTF8=1 PYTHONPATH=app python -m unittest workflow.test_visual_columns workflow.test_structure_gate workflow.test_content_structure`：73/73 通过。
- 实批仍保持未放行：视觉 artifact 为提供方失败，结构闸门仍需等待视觉证据并处理阅读数量、题号和来源硬阻断。

## 回退方式

恢复 `runner.py`/`auto.py` 对结构闸门非零结果的一律 `blocked` 处理，并移除视觉 artifact 的延期字段即可；不需要改动用户批次数据。
