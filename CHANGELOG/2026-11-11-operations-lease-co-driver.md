# 2026-11-11 运维租约不再被并行后端互毁（worker_busy 抖动修复）

## 一、背景/触发

用户反馈：AI运维面板反复出现

```
worker_busy: 批次执行锁已占用; name=Error
```

同批次【木牍教育】12年安徽中考英语在 `workflow/operations.json` 里 12:02:40–12:09:44 七分钟内出现
**22 次 `lease/process_replaced` + 21 次 `claim/claimed`、0 次完成**，`incident.status` 在
`claimed/queued` 之间来回跳，`execution_process.pid` 反复指向已退出的进程（34224 → 34912 → 7132 …）。

取证结论（三层）：

1. **宿主插件 JS 陈旧（用户那行报错的直接来源）**：GUI 端口 11040 的属主进程 9328 启动于
   `2026-11-09 20:30:45`，此后从未退出；`lib/index.js` 的 409 准入映射（`operationsHttpError`，
   提交 `ec9d95c` 2026-11-11 11:38）根本没被加载。旧代码 `if (!response.ok) throw new Error(result.error ...)`
   收到 409 也只抛裸 Error，`lib/operations-agent.js::errorText()` 只能渲染成
   `...; name=Error`（没有 `code=worker_busy`），宿主因此把它当成未知失败而不是「等锁」。
2. **后端租约互毁**：`app/workflow/operations.py::_expire()` 原来只比较
   `execution_process != _process_identity()` 就把 `claimed` 打回 `queued`，不区分
   「对方进程已死」与「对方是活着的并行后端」。同一数据根上跑着 8791/8792 两个后端实例时，
   两边互相作废租约，任何一轮都跑不完。
3. **热重启放大**：`lib/index.js` 在检测到 `app/**/*.py` 变化时会重启后端（`RESTART_DEFER_MS=20000`，
   上限 30 分钟），延后判据是 `/api/health` 的 `busy`；而 `busy` 只看 advance/upload/audio，
   不含运维租约 → 另一个会话每保存一次 .py 就把在途运维尝试判成 `process_replaced`。
   Windows 上 `reapStaleWorkbenchProcesses()` 首行直接 `return 0`，遗留后端不回收，才出现双后端。

## 二、改了什么

- `app/workflow/operations.py`
  - 新增 `_pid_alive(pid)`：Windows 用 `ctypes` 调 `OpenProcess(0x1000)` +
    `GetExitCodeProcess()==259`(STILL_ACTIVE) + `CloseHandle`；POSIX 用 `os.kill(pid, 0)`
    （`PermissionError`→存活）。**Windows 上绝不能用 `os.kill(pid, 0)`，它等于 TerminateProcess。**
  - 新增 `_foreign_owner(incident)`：`execution_process` 存在、不是本进程、租约未过期、且该 pid 仍存活。
  - `_expire()`：`claimed` 且租约未到期、原进程仍存活时**保持 claimed**，只记 heartbeat 相关证据，
    不再写 `process_replaced`；仅当「租约到期」或「原进程已消失」才打回 `queued` 并递增 `queue_generation`。
  - `claim()`：命中 `_foreign_owner()` 时抛
    `OperationsBusy("worker_busy: 另一后端实例正在处理该批次")`，交给宿主的 409 准入等待，不再抢锁重置。
  - 新增 `leased(root)`：只读扫描 `.auto` 批次，判断是否存在未过期且持有者存活的认领。
- `app/workbench/server.py`
  - 新增 `_operations_leased()`；`work_in_flight()` 在音频之后返回它 →
    运维回合进行中 `/api/health.busy=true`，插件的热重启因此延后（上限 30 分钟）。
- `app/docs/harness-operations.md`：补一段「多后端实例与租约」的口径说明（同上四点）。

## 三、没改什么 / 边界

- 没有放宽任何质量闸门：`explanations` 的 `EXPLANATION_SOURCE_UNVERIFIED` 仍是阻断项，
  运维仍须按受影响单元局部重生成，不许整卷重跑。
- 没有改上传边界，`upload_gate/upload/publish_gate` 依旧 skipped。
- 没有替用户打开运维开关（`plugin-state/md-upload-tiku/operations.json` 仍是 `{"enabled":false}`）。
- 没有改另一会话正在写的 `config/model-router.json`、`self_heal.py`、`upload_content.py` 等 WIP。

## 四、如何验证

- `python -m unittest workflow.test_operations workbench.test_health_busy` → **Ran 84 tests，全部通过**
  - 新增 `test_live_foreign_driver_keeps_its_lease`（并发后端持租约时抛 `worker_busy: 另一后端实例`，
    且不重置 `attempt_id/execution_process/queue_generation`、不写 `process_replaced`）
  - 新增 `test_dead_foreign_process_releases_the_lease`（原进程已死则正常重新认领，`queue_generation+1`）
  - 新增 `test_pid_liveness_never_signals_the_process`（`_pid_alive` 对自身为 True，对 0/None/非法/超界为 False）
  - 新增 `test_leased_tracks_unexpired_claims_only`
  - `test_health_busy.py` 新增 `test_operations_lease_is_busy` / `test_expired_operations_lease_is_not_busy`
- 现场只读复核：`op.leased(workflows)=False`、`op._pid_alive(34224)=False`、
  `_pid_alive(42604/9328/4)=True`、`_pid_alive(999999)=False`；`operations.json` 最后一条事件停在
  12:09:44，之后无新的 `process_replaced`。
- 复现前的重复后端（8792，pid 34912，11:59:25 启动、跑旧代码）已结束，只余 8791 单后端。

## 五、已知遗留

- **宿主插件 JS 必须整进程重启才生效**：9328 自 11-09 起未退出，`lib/index.js` 的 409 准入等待
  在运行中的宿主里没有加载。本次已按用户授权结束旧进程并重开应用。
- 全量 `python -m unittest discover -s workflow` 在 Windows 上仍有 5 个
  `workflow.test_candidate_cache` 错误：该模块用 POSIX `dir_fd`/`O_DIRECTORY` 打开目录，
  Windows 不支持（`PermissionError: [Errno 13]`），属既有平台限制，与本次改动无关。
- 另一会话仍在同一工作区改 `config/model-router.json`（default 指向余额不足的 deepseek-official，
  explanations 因此报 QUOTA）与 `self_heal.py`/`upload_content.py`，本次未纳入提交。
