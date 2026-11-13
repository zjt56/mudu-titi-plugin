# AI 运维升级重认领与隔离工具续跑

## 背景

批次 `23七上人教版课时A计划Unit2We’reFamily!第四课时Section` 已完成调查和根因记录，但 `promote` 因源码指纹变化返回 `inputs_changed`，旧 Agent 未自动释放并重新认领；随后隔离修复工具在 search→read_lines 之间未正确传递真实路径，重复触发拒绝并停在 recovery 队列。

## 修复

- `lib/operations-agent.js`：保留后端失败回执中的结构化错误码；`promote` 仅在明确 `inputs_changed` 且没有未知回执时执行 release → 同 session fresh investigation claim → 复用 finding → promote。任何 release/claim 未知结果都进入 quarantine，禁止盲重发。
- `app/workflow/operations.py`：`code_repair` 支持 search 返回的 cursor 直接用于 `read_lines`，并要求 read/read_lines/write 使用真实工作区相对路径；隔离工作区标记 tests_failed/损坏后，下一次 `request_code_repair` 自动创建新工作区而不是永久复用坏工作区。
- 工具 schema 明确禁止绝对路径、basename 和源码根路径，受保护控制面文件继续只允许 `read_evidence`，不开放 code_repair 写入。

## 验证

- `backend/operations-agent-investigation.test.cjs`：9/9 通过。
- 运维 Agent/宿主相关 Node 回归：85/85 通过。
- `workflow.test_operations_source_tools`：10/10 通过。
- Python `workflow.test_operations` + 隔离开发回归：85/85 通过。
- `node --check lib/operations-agent.js` 与差分检查通过。

## 当前批次边界

当前批次的历史隔离工作区已经记录为 tests_failed，未安装运行中插件代码，也未伪称恢复。更新后的 Agent 逻辑需要宿主重新加载后，才能继续消费该 queued/recovery 事件；重新加载后应从最新 finding 进入 fresh claim、隔离修复、测试、计划、安装和原 structure_gate 复验。上传、发布和审核差异仍不自动处理。
