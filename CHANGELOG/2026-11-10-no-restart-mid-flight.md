# 重启后端会杀掉在途模型调用：自愈诊断反复停在 diagnosing

## 背景 / 触发

2026-11-10 排查「AI 运维显示待命」时发现真正的问题不在运维：23 年安徽中考英语的解析阶段，自愈在 15:56、15:59、16:02 连续三次进入 `outcome=diagnosing` 后再无下文（无 `finished_at`、无 plan、无 error），对应的 `workflow/self-heal-runs/explanations-*/` 里只有 `input.txt`、空 `stdout.log` 和一句 `wf-router: stage=self_heal … -> provider=…` 的 `stderr.log`。

三次中断时刻与后端重启完全重合（插件重新 apply、python 后端 PID 换新、`plugin-state` 与 `backend/.local-agent.effective.json` 同一秒被重写）。机制很直接：

1. 后端工作全部在进程内线程 + 子进程里（advance 线程 → `workflow.agent run` → `node backend/wf-router.mjs` → `dsh` CLI 模型调用）。
2. 插件回收后端用 `terminateProcessTree`：Windows 上就是 `taskkill /pid <pid> /T /F`，**整棵树**都会被结束。
3. 触发回收的条件太宽松：`.py` 热重启不判断在途；`ensureBackendOnce` 一次 800ms 健康探测失败就当作后端已死并 retire。

结果是一次代码保存、一次插件重新 apply、或一次繁忙期的探测超时，都会把正在跑的模型调用连根杀掉——自愈台账上表现为「诊断中」永远不落结果，运维也拿不到失败事件。

## 改了什么

- `app/workbench/server.py`：新增 `work_in_flight()`（`_advance_state` 有 running、`_advance_queue` 非空、切段任务在跑 → True；状态读不到时保守按忙），`/api/health` 返回 `"busy": work_in_flight()`。
- `lib/index.js`：
  - 新增 `backendBusy()`（读 `/api/health` 的 `busy`，探测失败按不忙，避免挂死的后端永远不重启）、`restartShouldDefer(busy, elapsedMs, maxMs)`（纯判定）、`deferRestart/runDeferredRestart`（每 20s 复查，上限 `RESTART_MAX_DEFER_MS = 30 分钟`）。
  - `restartBackendSoon`（`.py` 热重启）：先查 busy，忙则延后到空闲，不再立即 taskkill。
  - `ensureBackendOnce`（restart=false）：探测失败且自有子进程仍存活时，再给两次 4 秒窗口；应答即可判定健康，避免把繁忙误判为死亡。
  - `POST /workbench/restart`（手动重启）：忙时返回 `{ok:true, deferred:true, busy:true}` 并安排空闲重启。
- 新增 `app/workbench/test_health_busy.py`（6 例）与 `backend/backend-watch.test.cjs` 的 4 条断言（延后判定窗口、重启前查 busy、手动重启延后、健康接口带 busy、探测失败再给长窗口）。

## 没改什么 / 边界

- **发布协调器（release coordinator）的重启路径没动**：安装发布必须是宿主独占、单一的重启，不能为了等在途任务而拖住代码安装；它仍走 `ensureBackend({restart:true})`。
- 插件卸载时的 `stopBackend()` 仍是立即回收（进程生命周期结束，不能把子进程留成孤儿）。
- 延后不是“永不重启”：忙超过 30 分钟仍会重启并打日志，避免代码更新被永久挡住。
- 没有把 `busy` 当成“不能重启”的绝对条件：探测失败（后端真的挂死）时 `backendBusy()` 返回 false，仍会正常回收。

## 如何验证

- `python -m unittest workbench.test_health_busy` → **Ran 6 tests, OK**（空闲/running/排队/切段/已结束/状态不可读六种判据）。
- `python -m unittest workbench.test_upload_status workbench.test_health_busy` → **Ran 20 tests, OK**（20 个用例全部通过）。
- `node --check lib/index.js` 通过；`node --test backend/backend-watch.test.cjs` → **5 pass / 0 fail**。
- `node --test backend/backend-lifecycle.test.cjs`：13 tests、5 pass / 8 fail，与改动前基线**完全一致**（本机 `python3` 是 WindowsApps 空壳存根、且用例依赖 POSIX 信号语义），非本次引入。

## 回退方式

删除 `blocker_outcomes` 之外的改动：把 `restartBackendSoon` 的 busy 判断、`ensureBackendOnce` 的两次长探测、手动重启分支的延后去掉即可；`/api/health` 多出的 `busy` 字段对旧插件无副作用。数据库/批次数据无迁移。
