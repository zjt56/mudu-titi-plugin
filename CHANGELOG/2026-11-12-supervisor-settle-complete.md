# 2026-11-12 完成批次监督台账收敛为 complete

## 背景 / 触发

批次 `05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec` 与 `07…第三课时Sec` 的实际本地阶段都已跑完
（07 的 `format_gate` 19:18:00 completed、`flow_done=true`），但工作台「继续处理」区仍显示
`workflow/supervisor.json` 的 `status="scheduled"`、`no_progress_attempts=3` 和上一条 `last_result`
（「闸门未通过」/「异常已交 AI 运维分析」）。原因是监督器在 `_has_work()` 为假时**直接 pause**，
`_tick_batch` 里 `local_complete → status="complete"` 的分支再也不执行，台账永远停在最后一次失败派发的快照。

## 改了什么

`app/workflow/supervisor.py`

1. `_tick_batch` 的完成分支：`local_complete` 为真时把台账**归一**为完成态——`status="complete"`、
   `next_retry_at=0`、`retry_reservation_at=0`、`no_progress_attempts=0`、清 `escalation`，
   并把 `last_result` 改成 `{"action": "complete", "status": "passed", "reason": "本地阶段已全部完成；剩余人工闸门等待确认"}`。
   只要台账还有陈旧重试字段就重写（不再要求 `status != "complete"`，避免「状态已是 complete 但字段仍是旧失败」）。
2. 新增 `settle(now=None)`：在暂停前对「本地阶段已全部完成」的批次补写最终台账，仅处理 `local_complete` 为真、
   非远端边界、能拿到批次锁的批次；未完成阶段继续欠账的批次一律不碰（不伪造完成）。
3. 看门狗 `watch()` 在进入空闲暂停前调用 `settle()`，异常只记日志不打断监督循环。

## 没改什么 / 边界

- 不把 `review_gate=needs_review` 的差异当通过：`complete` 只表示本地阶段跑完、等待人工闸门。
- 不派发、不重跑、不改任何批次产物；`settle` 只写 `workflow/supervisor.json` 台账。
- 未完成/受阻批次保持原调度状态；远端上传边界与回执仍按 `_remote_boundary` 跳过。

## 如何验证

- `python -m unittest workflow.test_supervisor workflow.test_supervisor_scheduling` → **Ran 47 tests, OK (skipped=1)**。
- 新增用例 `test_idle_pause_settles_a_finished_batch_instead_of_leaving_it_scheduled`：造一个
  `format_gate completed + review_gate needs_review/执行完成` 的批次，台账写成 `scheduled/attempts=3/旧 last_result/escalation`，
  断言 `has_pending_work=False`、`settle()` 后 `complete`、重试字段归零、`last_result.action=="complete"`、无 escalation、
  不触发 dispatch、二次 `settle` 幂等、未完成批次不被写台账。
- 真实批次：`WorkflowSupervisor(root, dispatch).settle()` → 05、07 均 `complete`；`GET /api/batches` 两条记录
  `advance.supervisor.status="complete"`、`counts.blocked=0`、`unverified=0`。

## 回退方式

`git revert` 本提交；台账会回到「暂停前最后一次写入」的旧快照，不影响任何阶段产物。
