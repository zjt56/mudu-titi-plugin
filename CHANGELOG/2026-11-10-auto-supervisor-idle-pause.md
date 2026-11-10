# 2026-11-10 自动巡检在没有试卷任务时暂停

## 背景

用户反馈：自动巡检（workflow supervisor）在没有试卷任务时本应暂停，但实际没有暂停。核对 `one_tick()`：它只要 `workflows/` 下有批次目录，就每隔 interval（默认 30 秒）扫描全部批次；即使所有批次的 `local_complete` 都为真、或已越过远端上传边界，也继续空转，并不断重写 `workflow/supervisor.json`。

## 改了什么

- `app/workflow/supervisor.py`
  - 新增 `needs_attention(folder)`：批次仍有本地自动工作（存在非 human 的 pending/running/failed/invalidated 阶段，或正被 worker 占用）才为真；`local_complete` 或已到远端上传边界（有回执/已上传）一律为假。
  - 新增 `has_pending_work(root)`：任一批次需要处理才为真。
  - `watch()` 在没有任何待处理任务时进入暂停：`health().paused=true`，阻塞在 `wake()` 事件上，不再按 interval 空转；由 `wake()` 或 `pause_poll`（默认 600 秒，仅作外部新建批次的安全复查）唤醒后继续。
  - 新增 `SupervisorConfig.pause_poll` 与 `WF_SUPERVISOR_PAUSE_SECONDS`。
  - 修正 `server._dispatch_supervised` 以 `reset_budget=False` 入队：此前监督器已提交的重试预算会被 `start_advance` 的 `reset_budget=True` 清零，导致 `no_progress_attempts` 永远归零、永远不到 exhausted（这也是“停不下来”的一层原因）。保留原有的非阻塞 `_lock` 探测。

- `app/workbench/server.py`
  - 新增 `_wake_supervisor()`；在上传批次推进、新建批次时唤醒暂停中的巡检，保证有任务时立即恢复。
- 回归 `workflow/test_supervisor.py::IdlePauseTest`：完成/远端批次不触发巡检、只有未完成本地批次才需要、无任务时循环确实停在 paused 且不 dispatch、出现新任务并 `wake()` 后立即恢复。

## 没改什么 / 边界

- 有未完成本地批次时行为不变：仍按退避与 exhausted 冷却推进，仍只读小台账、不直接调模型。
- 不改变远端上传/发布边界，不自动上传；remains只驱动本地流程。
- 现网 12 个批次核对结果：`pending_work_batches=[]`、`has_pending_work=False`，即修好后的巡检应处于暂停态。

## 验证

- `workflow.test_supervisor`、`workflow.test_supervisor_scheduling`：46 项通过。
- `workflow.test_execution_state`/`test_delivery`/`test_progressive_pipeline`/`workbench.test_upload_status`/`workbench.test_media_upload_pipeline`：50 项通过。
- 全量 workflow：2066 项，2048 通过、17 跳过，1 失败 + 2 错误。其中 2 个错误是沙箱禁止 `ps` 的进程探活用例；1 个失败为 `test_structure_number_inference::test_native_extraction_warning_cannot_prove_number`，已用未改动的 HEAD 代码单独复现同样失败，属既有问题，与本次改动无关。

## 回退方式

回退本提交恢复“只要有批次目录就每 interval 扫描”的旧行为；`reset_budget=True` 的旧派发会重新把重试计数清零。
