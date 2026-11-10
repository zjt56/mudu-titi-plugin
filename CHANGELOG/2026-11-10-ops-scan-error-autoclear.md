# 2026-11-10 运维扫描失败提示自动消失

## 背景

用户反馈面板「AI运维」下的“运维扫描失败：fetch failed; ...code=UND_ERR_SOCKET”长期常驻，是很久以前一次网络抖动的遗留消息，早已恢复却一直显示。

## 改了什么

- `lib/operations-agent.js`
  - 每次成功扫描用独立 `lastPollAtIso` 记录成功时间，成功即清空 `lastPollError`。
  - 瞬时传输错误（socket 断开、超时、DNS 等）一律不写入 `lastPollError`，也不覆盖最近成功扫描时间。
  - `settleStalePollError()`：只要之后有更晚的成功扫描即清空；即便非瞬时错误，超过 30 秒无新失败也自动消退，不再永久钉在面板。
- `lib/client.js`：错误行补充说明“如已恢复，将在下次成功扫描后自动消失”，避免被误读为当前故障。
- 回归 `backend/operations-agent.test.cjs`：新增用例验证历史非瞬时扫描错误在后续成功扫描后被清空。

## 没改什么 / 边界

- 不隐藏真实服务故障：`configError`、`INVALID_INCIDENT_LIST` 等仍在发生时显示。
- 不改变接手、重试、回执与上传边界。

## 验证

- `node --test backend/*.test.cjs`：227 项，225 通过、0 失败（2 项既有跳过）。
- `node --check lib/operations-agent.js lib/client.js` 通过。

## 回退方式

回退本提交即恢复旧行为（历史扫描错误可能常驻）。
