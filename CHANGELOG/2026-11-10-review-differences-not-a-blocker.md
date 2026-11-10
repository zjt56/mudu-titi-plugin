# 2026-11-10 审核差异不再算卡点（诊断与运维自愈）

## 背景

用户反馈「木牍教育 23 年安徽中考英语」的卡点诊断显示：

> 1 处需处理
> 审核放行判断：审核存在未解决的质量差异，尚未放行
> 审核差异队列仍有 20 项未解决；上游交付未满足：review（已执行，当前产物或消费者校验未满足；status=completed）

用户明确纠正：**审核差异不应该作为卡点**。按根 `AGENTS.md` 硬规则 6，一审/二审与原标签不一致的条目是审核产出，必须完整留在 `07_review/manual-review-queue.json` 与 `review-gate.json` 的 `rounds.errors`，供人工判断模型/知识头的偏差；`review_gate=needs_review` 是设计状态，不是故障，也不阻断手动上传（规则 3）。

根因在 `app/workflow/diagnose.py`：

1. `review_gate` 处于 `needs_review + execution_completed + continue_unqualified + release_ready=false`（runner 在 `manual_only` 时写入）时，仍被当作 `blocking=True` 的卡点；`failure_class` 还落成 `agent_output_needs_retry`，`next_action` 建议“修正代理输出后重试”。
2. 卡点说明写“审核差异队列仍有 N 项未解决”，把人工差异当成闸门阻断原因；`_remedy` 还建议“按具体审核质量差异自动修正并复验”，与“禁止自动改标签/改判通过”冲突。
3. 该设计态的上游 `review` 被展示为“上游交付未满足”，进一步把已执行的审核说成未交付。
4. 同类误判还影响了运维自愈：该批次的事件来自 `review`（模型输出 JSON 语法错误），运维 Agent 却按 `review-gate.json` 的 `REVIEW_ROUND_FINDING` 对 12 个题目单元执行了 `regenerate_units(knowledge)`，等于用局部重生成自动改标签、消除差异（违反规则 6）。旧代码对这条路径没有确定性拦截。

## 改了什么

- `app/workflow/diagnose.py`
  - 新增 `_review_differences(record)`：精确识别 `review_gate` 的设计态（`needs_review + execution_completed + continue_unqualified + release_ready=false`）。
  - 该状态现在是 **非卡点**：`blocking=False`，`failure_class=quality_differences`，`next_action=人工判断审核差异；未放行但不阻断手动上传`；`unmet=[]`，不再把 `review` 说成未交付；`hint` 明确“差异是审核产出，人工判断，不自动改标签/改判通过”。差异仍完整保留在人工队列与 `review-gate.json`，`release_ready` 仍为 `false`。
  - 批量摘要改为“自动阶段已执行完成；N 项审核差异待人工判断（不阻断手动上传），未宣称放行”，不再显示“卡在审核放行判断”。
  - 新增 `_review_gate_blockers()`：`review_gate` 真正 `blocked` 时只列确定性阻断项（标签体检/流程图/协议失败等），过滤 `REVIEW_ROUND_FINDING` / `MANUAL_REVIEW_OPEN` / `REVIEW_FINDING_OPEN`；人工差异只作为“另有 N 项”说明，不再冒充卡点原因。
  - 删除了原先依赖 `dependency_satisfied(review_gate)` 的“质量备注/卡点”摇摆分支，统一走设计态判断。
- `app/workflow/operations.py::_regenerate_units`：事件阶段为 `review` / `review_gate` 时直接拒绝局部重生成，错误码 `review_differences_are_output_not_blocker`。审核协议失败应重跑审核阶段，不能用改内容/改标签消化差异。
- `lib/operations-agent.js` 的 `OPERATIONS_PROMPT`、`app/docs/harness-operations.md`：写清审核差异不是卡点，禁止用 `regenerate_units` 自动改标签/改判通过/消除差异。
- `app/docs/pipeline.md`、`app/docs/review.md`、根 `AGENTS.md` 硬规则 6：同步“卡点诊断与运维自愈同样适用”的落地口径。

## 没改什么 / 边界

- 不改变审核闸门结论：`review_gate` 仍是 `needs_review`、`release_ready=false`，没有放行、没有改标签、没有清空人工队列，也没有自动 resolve 任何差异。
- 不改变调度与上传边界：规则 3 的手动上传路径不变；`format_gate` 仍按 `continue_unqualified` 策略执行。
- 未回滚该批次此前已经发生的知识点局部重生成（`04_knowledge/knowledge.json`，备份 `.bak` 保留），也未改任何题目数据；本轮只修代码与展示口径。若需要恢复原始知识点标签，应另按用户明确要求执行并重跑下游。
- 没有把“差异”当通过：诊断只降低展示层阻断，不改变质量结论。

## 验证

- `workflow.test_diagnose`：44 项通过。新增/改写用例覆盖：设计态差异 `blocking=False`、`blocking_count=0`、`blocked_at=None`、摘要含“N 项审核差异待人工判断”和“未宣称放行”；`blocked` 闸门只显示 `LABEL_AUDIT_INVALID` 等确定性阻断项而不显示 `REVIEW_ROUND_FINDING`；台账状态不被诊断改写。
- `workflow.test_operations`：61 项通过。新增用例验证 `review` 事件调用 `regenerate_units` 被 `review_differences_are_output_not_blocker` 拒绝，且不写反馈文件。
- 现场复核：目标批次 `diagnose()` 现返回 `blocking_count=0`、`blocked_at=None`，摘要为“自动阶段已执行完成；20 项审核差异待人工判断（不阻断手动上传），未宣称放行”；`review_gate` 条目 `failure_class=quality_differences`、`unmet=[]`。
- `node --check lib/operations-agent.js lib/client.js lib/index.js` 通过。

## 回退方式

回退本提交即可恢复旧诊断/旧运维行为（会重新把审核差异显示为卡点并允许局部重生成）。
