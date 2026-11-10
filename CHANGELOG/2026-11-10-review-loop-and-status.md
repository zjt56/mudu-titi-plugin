# 2026-11-10 修复 2023 卷“一直卡在内容审核”：审核 JSON 容错、运维误重生成、入口状态

## 背景

用户反馈“2023 的一直卡在内容审核”。核查 23 年批次：

1. 审核模型多次返回**结构完整但少一个根右花括号**或**字符串内含原始制表符**的 JSON；
   `_parse_response(strict=True)` 在审核模式完全不做修补，于是同一分片反复失败。
2. 审核失败被 AI 运维接手后，旧动作对 review 事件执行 `regenerate_units` /
   `upstream_rederive`，重跑了知识点与组装，改了 `knowledge.json`/`batch.json`；
   审核绑定的输入随之变化，完成态被立即作废，形成
   “审核完成 → 上游重生成 → 审核失效 → 再审核” 的死循环。
3. 一审/二审+差异待人工时，`derive_status` 仍返回 `reviewing`，界面一直显示“审核中”，
   20/21/22 年卷同样如此。

## 改了什么

1. `app/workflow/local_agent.py::_parse_response`：严格模式也允许
   - 结构闭合补齐（最多补 1–3 个 `}`/`]`，只补括号，不删改内容）；
   - 字符串内原始控制字符用 `strict=False` 重解析（仍拒绝 NaN、重复键）。
   真正截断/内容缺项仍解析失败并拒绝。
2. `app/workflow/operations.py`：
   - `regenerate_units` 对 review/review_gate 事件直接拒绝（差异是产出）；
   - `resume` 的 `upstream_rederive` 对 review/review_gate 拒绝，要求
     `retry_stage_with_fresh_context`，避免重派生上游导致审核绑定失效死循环。
3. `app/workflow/execution_state.py::derive_status`：review_gate 为
   `needs_review + execution_completed + continue_unqualified + 未放行` 且
   validate/format_gate 已完成时，入口状态返回 `pending_confirmation`
   （待确认/可上传），不再永久显示“审核中”；差异仍不放行、质量不通过。
4. 回归测试：审核严格模式补括号/控制字符；review 差异状态为待确认。

## 边界

- 只做结构闭合与空白字符容错，不猜测缺失内容，不自动改标签、不改判通过。
- 审核差异仍保留在 `manual-review-queue.json` / `review-gate.json`，人工判断。
- 上传/发布仍由用户手动确认，不自动提交。

## 验证

- 23 年卷重跑审核：`exit 0`，review=`completed`，review_gate=`needs_review`
  （15 项差异），`local_complete=True`。
- `workflow.test_local_agent + test_operations + test_execution_state + test_supervisor
  + test_diagnose`：全部通过（含新增用例）。
- 全量工作流与后端结果见提交说明。

## 回退

还原 `_parse_response` 修补块、operations 两个 guard 与 `derive_status` 分支即可；
已完成批次的既有审核产物不受影响。
