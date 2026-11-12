# 后端存活看门狗：排队批次不再因后端缺席静默停摆

## 背景（触发）
用户反馈「排队去的任务没有正常推进」。排查批次
`05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec`：

- `workflow/manifest.json` 在 2026-11-12T16:52:04 完成 `validate`，状态 `reviewing`，
  `review` 及之后全部 pending；
- 批次目录 16:52:21 之后**零写入**，直到 17:13:08 才出现 `operations.json`（21 分钟空转）；
- 17:11 的进程快照里**不存在** `python -m workbench.server`；17:13:09 宿主页面请求触发
  `ensureBackend()` 后端才启动，2 秒内即派发 pending 的 `review`（3 个 wf-router 分片）。

根因：批次推进队列（`app/workbench/server.py::_advance_worker_loop`）与 `WorkflowSupervisor`
都只活在 `workbench.server` 进程内。`lib/index.js` 的 `spawnBackend()` 在子进程 `exit` 时只清
`backend.proc / instanceId / ready / lastError`（原 lib/index.js:376-382），**没有任何重启路径**；
`ensureBackend()` 也只在宿主页面/接口请求时才被调用。于是后端一旦退出（插件重载、热重启失败、
崩溃），队列就没有任何自愈途径，界面持续显示"排队/处理中"，实际无人推进。

## 改了什么
- `lib/index.js`：在 `retireBackend()` 之后新增后端存活看门狗
  - `backendWatchdogTick()`：`backendChildAlive(backend.proc)` 为真则清零失败计数并跳过；
    否则 `await ensureBackend()` 自动拉起；连续失败按 `15s → 30s → 60s` 指数退避
    （`backendWatchdogDelay()`，上限 `BACKEND_WATCHDOG_MAX_MS = 60000`）；
    正在热重启（`backendRestartInFlight()`）或上一轮探测未结束时跳过，不抢重启、不重复启动。
  - `startBackendWatchdog({ intervalMs = 15000 })` 用自递归 `setTimeout` + `unref()` 排程，
    单实例、可停；`stopBackendWatchdog()` 清 timer。
  - 插件激活处新增 `ctx.effect(() => { startBackendWatchdog(); return () => stopBackendWatchdog(); },
    'wf-plugin: backend watchdog')`；`__plugin` 导出新增三个函数便于测试。
- `backend/backend-lifecycle.test.cjs`：测试切片返回对象补三个导出，新增 3 个用例
  （后端退出后看门狗拉起、健康后端不重复 spawn、调度器幂等且可停）。

## 没改什么 / 边界
- 看门狗只在**子进程不在**时动手，不因健康探测偶发超时就杀掉繁忙后端；不介入进行中的热重启。
- 不改 `ensureBackend()` 的端口选择、实例身份校验和退役逻辑；不改变"外部健康监听不被收养"的边界。
- 不改批次数据、manifest、审核队列、上传边界；不改 `review_gate=needs_review` 的设计语义。
- 同一天另有一条独立的 `format_gate` 口径改动（表格单元格图片降为 warning），见
  `CHANGELOG/2026-11-12-cell-media-warning.md`；两者互不依赖。

## 如何验证
- `node --check lib/index.js lib/client.js`：通过。
- `node --test backend/backend-lifecycle.test.cjs`：16 项，8 通过、8 失败。失败项在
  未修改的 HEAD 基线（`git show HEAD:lib/index.js` + HEAD 测试文件，隔离目录运行）上完全一致
  （5 通过 / 8 失败），属本机既有问题（含 `python3` 不可用导致真实后端用例失败），非本次回归；
  新增 3 个看门狗用例全部通过。

## 回退
回退本次 `lib/index.js` 与测试文件的改动即可；看门狗不写任何持久状态，回退后行为与之前一致。

## 关联
- ISSUES/open：`2026-11-12-排队批次静默停摆-工作台后端退出后无人自动拉起.md`（stage=queue，
  check=WORKBENCH_BACKEND_ABSENT）。
