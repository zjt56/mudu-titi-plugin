# 局部修复也先走直抄；面板显示“流程推进中”而非待命

## 背景 / 触发

1. 23 年安徽中考英语的 `EXPLANATION_SOURCE_UNVERIFIED`（解析未能唯一逐字绑定原题）连续 4 轮走了模型诊断：每轮约 3 分钟模型调用 + 180 秒冷却，合计约 25 分钟，最后由“按来源坐标直抄”一次通过。原因是 `app/workflow/agent.py` 的直抄快路径带了 `not scoped` 条件，而该阶段当时的修复都是 scoped（`workflow/repair-feedback/explanations.json` 里 `scoped_units: true`），于是每次都绕开确定性路径。
2. 面板底部 AI 运维在真实卡点存在时仍显示“已启用 · 待命”：`lib/client.js` 的服务状态只按 `incidentCount > 0` 判断，而自愈正在判断/重试（尚未判定为卡点、还没有运维单）的阶段完全不显示；用户据此以为运维没启动。

## 改了什么

1. **局部修复先试直抄**（`app/workflow/agent.py`）：`explanations` 阶段不再因 `scoped` 跳过 `explanations_direct.install`。直抄成功即按确定性路径交付（`model_calls: 0`）；直抄不接受且当前是 scoped 修复时，吞掉该 `ValueError` 回到通用任务路径（带反馈继续），非 scoped 仍原样抛错，避免把“直抄判不了”伪装成成功。
2. **运维面板口径**（`lib/operations-agent.js`、`lib/index.js`、`lib/client.js`）：运维轮询的 `hasWork` 现在返回 `{has_work, batches}`，管理器记录这些批次并在 `status()` 暴露 `healingCount` / `healingBatches`；面板文案新增“流程推进中（N 个批次，自愈进行中）”，只有既无卡点也无在推进批次时才显示“待命”。`hasWork` 的 false/true 兼容仍保留（旧布尔返回等价于 `has_work`）。

## 没改什么 / 边界

- 没有放宽任何校验：直抄仍要过 `agent_protocol` 的交付校验与消费者校验，失败照旧报错。
- `healingCount` 只是显示口径，不产生运维单、不改变 `incidentCount`，也不代表该批次已通过；审核差异仍是产出（`review_gate=needs_review` 照旧等人工判断）。
- 后台套接（`lib/index.js`）需要在宿主内重新加载插件后才生效；Python 侧改动由后端热重启自动接管。

## 如何验证

- `workflow.test_direct_explanations_delivery`：7 个用例全部通过（新增 scoped 直抄与回落两条）。
- `workflow.test_direct_explanations_delivery + workflow.test_operations + workflow.test_self_heal + workbench.test_health_busy`：88 个用例全部通过（隔离 `TEMP` 后运行；与后台进程共享全局模型锁时会出现 WinError 13，属环境争用）。
- `backend/operations-agent.test.cjs + operations-ui.test.cjs + operations-host.test.cjs`：52 个用例全部通过（新增“有批次推进时显示 healing、空闲后清零”两条）。
- `node --check lib/operations-agent.js lib/index.js lib/client.js` 均通过。

## 回退方式

回退本次提交即可：`agent.py` 恢复 `and not scoped` 条件；`lib/operations-agent.js`、`lib/index.js`、`lib/client.js` 恢复纯 `incidentCount` 文案。
