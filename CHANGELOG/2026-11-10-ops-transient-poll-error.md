# 2026-11-10 运维扫描偶发网络错误不再常驻显示

## 背景

用户反馈面板上“运维扫描失败：fetch failed; name=TypeError; cause: other side closed; name=SocketError; code=UND_ERR_SOCKET”一直存在。原因是运维轮询把一次偶发 socket 断开写进 `lastPollError`，而该字段只在特定两条成功路径清空；一次错误即可长期钉在面板上，即使后续扫描已成功。

## 改了什么

- `lib/operations-agent.js`
  - 新增 `isTransientPollError()`：识别 `fetch failed`、`other side closed`、`socket hang up`、`aborted` 及 `ECONNRESET`/`UND_ERR_SOCKET`/`ETIMEDOUT`/`ENOTFOUND` 等瞬时传输错误。
  - `tick()` 捕获错误时：瞬时错误不写入 `lastPollError`（不当作常驻服务故障），并记录时间戳。
  - `poll()` 每次请求成功后立即清空 `lastPollError`（包括“无待接手事件”的早退路径）。
  - `status()` 返回前调用 `settleStalePollError()`：非瞬时错误超过 30 秒没有成功扫描也会自动消退，不再永久显示。
- 回归 `backend/operations-agent.test.cjs`：新增用例验证 socket 类错误被判为瞬时、瞬时错误一次扫描后即清空、非瞬时错误码不被误判。

## 没改什么 / 边界

- 不隐藏真正的服务故障：`configError`、`INVALID_INCIDENT_LIST` 等配置/协议错误仍照常显示。
- 不改变接手、重试、回执与上传边界；只是不再把一次网络抖动当成长期状态。
- 扫描时间与“超过20秒未更新”提示逻辑保持不变。

## 验证

- `backend/operations-agent.test.cjs`：42 项通过。
- 后端全量 `node --test backend/*.test.cjs`：225 项，223 通过、0 失败（2 项为既有跳过）。
- `node --check lib/operations-agent.js lib/client.js lib/index.js` 通过。

## 回退方式

回退本提交恢复旧行为（偶发网络错误会重新常驻面板）。
