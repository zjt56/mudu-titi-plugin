# AI 运维自动接收、修复与复验闭环

## 背景

用户要求 AI 运维像当前会话一样接收批次报错：自动读取报错证据、分析原因、执行受控修复、复验原失败检查并续跑，而不是只显示建议或等待用户再次点击。

## 改了什么

- `app/workflow/runner.py`：显式 `runner run` 的失败阶段在 `_single_run_lock` 释放后自动调用 `operations.handoff_if_available`；成功、dry-run、审核差异和健康推进不重复建单，运维异常不改变原 runner 退出码。
- `lib/operations-agent.js`：普通 HTTP 请求仍为短窗口；`code_repair operation=test` 使用 930 秒、安装使用 120 秒动作级窗口。窗口仍受会话最大时长、AbortSignal、停止和未知回执保护，不会因长测试盲目重发。investigation 成功 promote 为 repair 后清零 `readOnlyCount` 与 `noProgress`。
- `app/workflow/operations_development.py`：隔离修复 allowlist 扩展到 `app/scripts/**/*.py`，支持真实校验脚本的读、改、测试、变更计划和 CAS 安装；锁定控制面、秘密文件、符号链接、越界路径仍拒绝。
- `app/workflow/operations.py`：普通流水线在旧运维事件之后自行通过时仍保留当前消费者复验，但明确写入 `resolution_source=pipeline_progress`、`resolved_by_operations=false`。
- `lib/client.js`：该状态显示为“流水线已复验”，不冒称“AI 运维修复完成”。
- 本轮用户已通过可信本地配置接口启用源码根绑定的自治修复；上传、发布、审核差异裁决仍不在自治权限内。

## 验证

- Node 运维宿主/Agent/恢复/自治/发布相关测试：85 项通过。
- Python 运维、首个阻断、CLI 交接、隔离开发、自治授权测试：151 项通过。
- 针对流水线自行恢复语义的 13 项 Python 回归通过。
- `node --check lib/operations-agent.js`、`git diff --check` 通过。
- 真实批次 11 的历史事件确认只有 `discover` 与 `reconcile`，没有 investigation/promote/repair/resume；现在同类 reconcile 会标记为流水线复验。

## 边界

这次修复让故障进入自动运维闭环，不把审核 `needs_review` 当故障、不自动上传发布，也不把模型空闲、请求完成或普通流水线后来通过冒充 AI 修复。真实代码安装仍要求测试证据、安装计划、宿主重启和原失败检查复验。
