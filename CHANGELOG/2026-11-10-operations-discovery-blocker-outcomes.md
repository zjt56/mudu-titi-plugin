# 运维建单只认两种 outcome，预算耗尽的卡点永远不接手

## 背景 / 触发

用户反馈（2026-11-10 16:01）面板显示「AI 运维 已启用 · 待命 · 未接手」，而同页面的卡点诊断明确列出 23 年安徽中考英语的解析卡点，看起来「运维遇到卡点却没启动」。

排查后确认两层原因：

1. **面板「待命」只由 `incidentCount > 0` 决定**（`lib/client.js`）。运维没有建出 incident，界面就写“待命”，即使 `/api/operations/work-state` 已经返回 `has_work: true`。
2. **`operations._discover` 的过滤条件只认 `repair_failed` / `retry_failed`**，而 `self_heal.BLOCKER_OUTCOMES` 是 `{repair_not_applicable, repair_failed, retry_budget_exhausted, gate_retry_budget_exhausted}`。也就是说“修复不可用”“重试预算耗尽”“闸门重试预算耗尽”这些**自愈已经判定卡住**的结果，运维侧一个都收不到，只能永远等 `repair_failed`。

本次 23 年批次恰好是第三种情形：15:56 / 15:59 / 16:02 三次自愈诊断在模型调用刚开始时被打断（事件停在 `outcome=diagnosing`，无 `finished_at`，`stderr.log` 只有 wf-router 的选模型行），16:06:43 第 4 次重试成功，16:07:00 解析阶段完成。所以这一轮并不是运维该接手的场景，但“预算耗尽”这条路确实断了。

## 改了什么

- `app/workflow/operations.py::_discover`：失败事件过滤改为 `set(self_heal.BLOCKER_OUTCOMES) | {"retry_failed"}`，并在注释里写明只接管“自愈已判定卡住”，不接管 `retry_scheduled` / `upstream_escalated` / `content_retry_with_feedback` 这类仍在推进的状态。
- `app/workflow/test_operations.py` 新增两条回归：
  - `test_discovery_accepts_every_recorded_blocker_outcome`：`repair_not_applicable`、`retry_budget_exhausted`、`gate_retry_budget_exhausted` 都必须建单；
  - `test_discovery_ignores_retries_still_in_progress`：`diagnosing`、`retry_scheduled`、`upstream_escalated`、`content_retry_with_feedback` 都不建单。

## 没改什么 / 边界

- 没有改“何时该建单”的其他门槛：仍要求 stage + 精确 detail 匹配、仍排除限流类错误码、仍要求事件带真实签名。
- 没有改面板文案，也没有把“有卡点诊断”直接等同于“运维应接手”。自愈仍在预算内时由自愈处理，运维只接手它判定卡住之后的场景。
- 没有自动裁决审核差异；知识点差异仍是产出。

## 如何验证

- `python -m unittest workflow.test_operations` → **Ran 63 tests, OK**（新增 2 条，含 3 + 4 个 subTest 分支）；63 个用例全部通过。
- 23 年批次 16:07 后自行恢复：`explanations completed 16:07:00` → `assemble completed 16:07:07` → `meta completed 16:07:08` → `validate completed 16:07:10` → `review running`。

## 回退方式

把 `_discover` 里的 `blocker_outcomes` 恢复为 `{"repair_failed", "retry_failed"}` 并删除两条测试即可；不涉及数据迁移。

## 遗留

- 三次 `diagnosing` 事件没有收尾（无 `finished_at`），台账上看不出“诊断被中断”这件事。若要留证，应在重新诊断前把过期未收尾的 `diagnosing` 事件标成 `diagnostic_interrupted` 并落 ISSUES。
- 中断时刻与后端进程重启吻合（16:02:36 插件重新 apply、python PID 换新），具体是谁重启后端仍需单独确认。
