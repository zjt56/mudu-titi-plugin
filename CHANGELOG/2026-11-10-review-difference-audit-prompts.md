# 2026-11-10 审核差异保护复审与提示词约束

## 背景

用户要求“审核上面改动，加强提示词的约束”。复审 18775ac，同时由独立只读审查复现诊断问题。

## 审查发现与修复

1. 旧代码把“没有 blocking 项 + 有审核差异”直接当成自动阶段全部完成；review 或 format_gate 仍 pending/running 时也会错误宣称完成。现在摘要先显示真实失败，再显示当前未完成本地工作；仅全部本地阶段满足执行状态（含 rebuild_required 检查）时使用“自动阶段已执行完成”。差异继续非阻断。
2. review_gate 的 FAILED/INVALIDATED 分支读取旧闸门报告覆盖当前异常/内容失效原因。现在保留当前执行错误和失效原因；blocked 缺少非差异错误码时只报告证据待核验，不编造确定性阻断项。
3. self_heal 的生产者定位仍能把 REVIEW_ROUND_FINDING 中的 field=knowledge 或 producer_stage 当成内容故障，绕到上游重判。现在三个差异码（REVIEW_ROUND_FINDING / MANUAL_REVIEW_OPEN / REVIEW_FINDING_OPEN）先行返回无生产者，真实 KNOWLEDGE_INVALID 仍正常定位。
4. 审核员正文仍有笼统“修不了回上游重跑”；更关键的是一审/二审使用 agent_roles.section 的专用段落，不直接载入角色正文。两处同时加强：差异正常交付、独立 assessment、盲审隐藏不等于标签缺失、禁止迎合旧结论/改正式标签/自动 resolve/dismiss/重审直到差异消失。仅修 JSON/键集/覆盖/引证/绑定协议和未执行目标。
5. agent_tasks 的通用审核规则、self_heal 计划提示、operations 运维提示同时强化：写前需要当前检查/错误码/批次/题号，不能以历史事件、旧报告、模型建议或差异数量授权内容重生成；禁止用 resume/upstream_rederive/rejudge_knowledge/apply_repair 换动作绕过差异保护。旧 execution_completed、0 卡点、空队列均不证明当前审核或最终格式阶段完成。
6. 修正二审角色的抽样范围表述：计划 TARGET 为准，整组题型按整组，独立单题按小题。

涉及 diagnose.py、self_heal.py、agent_roles.py、agent_tasks.py、prompts/roles/review.md、lib/operations-agent.js 及对应回归；review.md、harness-operations.md 同步说明。

## 边界

本轮未调用模型、未重跑或编辑任何用户批次，未清空/改判审核差异，未修改正式标签或上传授权。新提示词由后续任务构建使用，不回写已冻结的历史任务。审核质量仍未放行；本地格式检查和手动上传保留现有边界。

## 验证

- 针对性工作流回归：291 项通过（diagnose/self_heal/agent_roles/operations/agent_tasks/review_rounds/review_consensus）。新增用例覆盖 pending/running 不误报完成、历史 gate 不覆盖当前失败、缺证据不编造阻断、差异码不路由到内容生产者，以及实际任务提示词约束。
- 后端全量：212 项，200 通过、10 失败、2 跳过。失败集中于 upload-flow-redesign / upload-stage-flow 的前端流程/删除入口旧断言；本轮未修改 lib/client.js 或这两个测试文件（git diff HEAD 对应路径为空）。未为通过测试修改无关 UI。
- 插件 index/client/operations-agent 语法检查通过。
- 全量 workflow：1994 项，1975 通过、17 跳过、2 错误；两条错误均在未改动的 test_local_agent_ownership 中，沙箱拒绝执行 ps（PermissionError: Operation not permitted），不是断言失败。未绕过沙箱限制重试。
- 单独运维后端回归：37 项通过，覆盖提示词约束与会话/回执/权限边界。
- git diff --check 通过。
- git fetch origin master 在 15 秒限制内超时；未进行覆盖性合并或强推。

## 回退

回退本次提交即可；不涉及批次产物回滚。保留前一提交的审核差异非阻断设计。
