# 审核卡片状态与协议失效自动补审

## 背景 / 触发

用户看到批次卡片显示「一审未完成 · 二审未完成」，但点开明细，两轮审核都已有完整结论。检查确认：

- `app/workbench/server.py::review_snapshot` 只用 `review.evaluate_rounds(...)` 返回的 `checked_rounds` 构造轮次；当审核记录是**旧协议**（`review-prompt-v9`）时，记录合法、确实执行过，但当前契约（`review-prompt-v10`）不接受，轮次被整体丢弃，返回 `completed=false / reviewed=0`。
- `lib/client.js` 只读 `r.completed`，于是把「有真实执行记录、只是已过期」显示成「未完成」。
- 同时，`manifest` 里 review 仍是 `completed`，自动调度看到终态就不再补审，形成「卡片说未完成、流程说已完成、实际上没人重跑」的三方不一致。

## 改了什么

1. `app/workbench/server.py::review_snapshot`
   - 每轮新增 `state`：`completed`（当前契约有效）/ `needs_refresh`（有历史执行记录但已过期）/ `not_started`（确无记录）。
   - 新增 `historical_reviewed`：只从**唯一且带 `execution_id`/非空 `checks`** 的历史记录投影历史覆盖数。
   - `completed`/`reviewed` 仍只来自当前有效记录，**绝不把旧记录当当前有效结论**。
2. `lib/client.js` 卡片文案按 `state` 显示：「已完成 N 单元 / N 抽样核对」、「已有审核记录，待更新」、「尚未执行」。
3. `app/workflow/auto.py` 新增 `_refresh_completed_review(batch_dir, manifest)`：review 处于终态且 `07_review/review-rounds.json` 确有轮次记录、但 `review.evaluate_rounds(...).execution_completed` 为假时，`invalidate_stage` 并保存，下一轮自动真实补审。无轮次证据（旧批次/夹具）时不改判。
4. 测试：`app/workflow/test_review_refresh.py`（过期即补审、真实差异不重复审、无证据不误判）、`app/workflow/test_workbench_review_status.py` 新增旧轮次状态投影用例。

## 没改什么 / 边界

- **不放宽审核契约**：`review_legacy.SUPPORTED_CURRENT_CONTRACT`、盲审两轮、`execution_id` 独立来源等校验一律不动；旧结论不会被当成当前有效。
- **不伪造质量通过**：只要存在真实差异，`quality_passed` 仍为 false，`review_gate` 保持 `needs_review`，不人工关闭、不强制放行。
- 只按「执行是否有效」决定补审；差异多少不触发重复审核。
- 不自动上传 / 发布。

## 验证

- `python -m unittest workflow.test_review_refresh workflow.test_workbench_review_status workflow.test_runner_auto`：**48 项通过，2 项跳过**（退出码 1 仅因 PowerShell 把 stderr 当错误流）。
- `node --check lib/client.js` 通过；`git diff --check` 无空白错误。
- 实批 `【木牍教育】20年安徽中考英语` 接口验证：`execution_completed=true`，一审 `state=completed / reviewed=34 / ok=34`，二审 `state=completed / reviewed=7 / ok=7`，`open_findings=13`、`quality_passed=false`（13 项真实标签差异仍待人工决定）。

## 回退方式

`git revert` 本提交即可；`state` / `historical_reviewed` 为新增只读字段，前端旧逻辑读 `completed` 仍可用。
