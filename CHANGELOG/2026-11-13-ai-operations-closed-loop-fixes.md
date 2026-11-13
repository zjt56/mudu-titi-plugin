# AI 运维接单后闭环链路修复（批次23）

## 背景

用户反馈批次 `23七上人教版课时A计划Unit2We’reFamily!第四课时Section` 明明卡住，AI 运维却没有实际修复。查明不是“没接单”，而是接了单后修复/续跑链路五处断点，导致反复空转：

1. investigation→promote 遇到 `inputs_changed` 不自动释放并重新认领，停在调查。
2. 隔离代码工作区按文件唯一复用，测试失败的工作区被错误标 `verified` 且跨事件复用，后续答案故障读到旧工作区。
3. `code_repair` 未把 search 的 cursor/真实相对路径正确传给 read_lines，触发 `source_not_allowlisted`/`no_substantive_progress`。
4. 原生内容 Agent 对同 identity 的 `failed` 任务永久复用，上游瞬时恢复后也不会重新调用模型。
5. 历史 `failed` 阶段即使上游瞬时故障消退也不会冷却重排，普通调度直接跳过。

## 修复

- `lib/operations-agent.js`：保留后端失败码；`inputs_changed` 且无未知回执时 release→同 session fresh investigation claim→复用 finding→再 promote，未知回执不重发。
- `app/workflow/operations.py`：隔离工作区操作/恢复/结算按 `incident_id` 绑定，仅 `restarted_pending_verify` 可由原检查结算；历史纯瞬时事件迁移为 `transient_deferred`/`superseded`（保留未知回执与审计，不称 resolved）。
- `app/workflow/operations_development.py`/`operations.py`：隔离范围覆盖 `app/scripts` Python；坏工作区自动新建；`code_repair` 支持 cursor 与真实路径。
- `app/workflow/content_agent.py`：仅在受信宿主 failed 回执、精确上游瞬时错误、无候选/验收/未知结果且已冷却时，限量（≤3）重派新任务，旧回执不变。
- `app/workflow/auto.py`：默认桥精确上游错误有限冷却；原生任务核对任务身份/批次/阶段/失败状态且无候选、无确定性失败；历史仅单次完整绑定的失败答案可冷却重排，混合真实阻断保留。

## 验证

- 运维/内容/调度回归合计 **184 项通过**（Python 3.12，临时目录与模拟宿主）。
- 真实批次证明：重启后端加载新代码后，`answers` 由 `failed` 转为 `pending`（“已核验原生任务上游瞬时故障，冷却后按正常调度续跑”），冷却结束后实际重派新任务 `41844b14`（`redispatch_of 9e49642e`）；`knowledge` 经普通重试已完成。

## 边界

当前 `answers` 仍因上游模型服务持续返回 `Upstream HTTP/2 stream failed` 处于冷却重试，这是外部 provider 可用性问题，不是代码或题目缺陷；服务恢复后应自动续跑。AI 运维不上传、不发布、不裁决审核差异，真实内容/校验错误仍阻断。
