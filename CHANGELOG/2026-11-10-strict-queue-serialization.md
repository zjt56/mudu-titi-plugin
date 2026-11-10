# 2026-11-10 修复排队区串行：前一份未完成时不得启动后一份

## 背景

用户反馈：前一个试卷遇到卡点或问题时，排队区里其他试卷会自动开始任务——这是错误逻辑。必须等上一份任务全部完成、并从排队区移除后，才允许启动下一份。

根因：`WorkflowSupervisor.one_tick` 每个 tick 会把**所有**活跃未完成批次都通过 `_dispatch_supervised → start_advance → _launch_advance` 派发进全局 `_advance_queue`，队列只是“同一时刻跑一个”的先进先出；当第一个批次卡在某阶段（`flow_done` 仍为 false）时，它只是结束了自己这一趟，工作线程随即取出下一个批次继续跑，于是“其他试卷自动开始”。

## 改了什么

- `app/workbench/server.py`
  - 新增 `_queue_batch_order()` / `_queue_head()`：按 `created_at` 升序列出仍处于「排队区」的批次（未上传云端、且 `execution_state.local_complete` 为假），即尚未完成、尚未移出排队区的批次。
  - `_launch_advance`：派发前做严格串行闸门——若目标批次不是排队区队首（前面仍有未完成批次），直接返回 `{started:False, waiting:True, waiting_for:<队首>, reason:…}`，不进入执行、不消耗模型；前端显示“等待前一份…”。
  - `_advance_worker_loop`：出队后再次核对队首（防御并发/历史入队），非队首则标记 `waiting` 并跳过，绝不越序执行。
- `lib/client.js`：`recoveryProgressText` 显示“排队等待：前一份试卷全部完成并移出排队区后再开始”；推进按钮在 `waiting` 时禁用并显示“等待前一份…”。

## 没改什么 / 边界

- 不改变 `flow_done`/上传判定，不改变审核与上传边界；只改“谁可以开始”。
- 真正的串行只针对「排队区未完成」批次；已 `flow_done`（如仅剩人工审核差异）会移出排队区，不阻塞下一份。
- 若前一份长期卡在需要人工/输入的阶段，后续批次会一直等待（符合用户要求的严格串行）；这是预期行为，不是故障。

## 验证

- `workbench.test_upload_status` 新增：两批次时仅队首可启动、后者 `waiting` 且不入队；前一份 `flow_done` 后队首顺延；已上传/已完成批次移出队列。
- `workbench.test_upload_status`、`test_list_projection`、`workflow.test_supervisor`、`test_supervisor_scheduling`、`test_workbench_review_status` 共 62 项通过。
- `node --check lib/client.js` 通过。

## 回退方式

回退本提交即可恢复原派发逻辑（会重现“前一份卡住后一份自动开始”）。
