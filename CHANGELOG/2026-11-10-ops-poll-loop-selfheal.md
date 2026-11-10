# 2026-11-10 运维自动巡检循环自愈与存活检测

## 背景

用户反馈：已有试卷任务，但 AI 运维“自动巡检”看起来没开启。实测运维服务 `running=true`，但 `lastPollAt` 停在很早的时间、`pollCount=2`，说明 5 秒巡检循环已经停摆，只是一次偶发 `fetch failed` 后循环不再推进。

## 根因

1. `tick()` 里 `poll()` 若同步抛错（后端未就绪 / 内部早退），`polling` 标志可能没有正确复位，循环卡在“永远认为正在轮询”。
2. 巡检不主动确认后端存活：workbench 重启后端口变化，循环会对死端口反复失败，不会自动重连。
3. `start()` 只建一次定时器，一旦定时器被清掉（stop 后或竞态），进程还标记 `running` 却没有任何巡检。

## 改了什么

- `lib/operations-agent.js`
  - `tick()` 改为 `Promise.resolve().then(() => poll())`，同步抛错也能复位 `polling`，不会锁死循环。
  - 新增 `ensureBackendReady` 钩子，每次巡检前先确认后端就绪，服务重启后自动恢复。
  - 新增 `ensureLoop()`：`start()` 与 `status()` 都会检查并按需重建定时器，保证 `running` 时一定存在巡检循环。
  - `status()` 新增 `loopAlive`、`pollStale`，让面板可区分“循环停止/扫描超时”。
- `lib/index.js`：为运维 manager 提供 `ensureBackendReady`（未就绪时调用 `ensureBackend()`）。
- `lib/client.js`：提示区分“自动巡检循环已停止（正在自动恢复）”与“超过 20 秒无成功扫描（可能后端重启中，正在重试）”。
- 回归 `backend/operations-agent.test.cjs`：新增“巡检循环自愈、后端未就绪不锁死”。

## 没改什么 / 边界

- 未改变运维接手、动作权限、回执、预算与上传边界。
- 无任务时仍不创建运维会话；只是保证有任务时巡检循环一定在转。

## 验证

- `node --test backend/*.test.cjs`：228 项，226 通过、0 失败（2 项既有跳过）。
- `node --check lib/operations-agent.js lib/index.js lib/client.js` 通过。

## 回退方式

回退本提交恢复旧循环行为（后端重启或一次同步错误后可能停摆）。
