# 卡点：本地阶段已跑完的批次监督台账停在 scheduled 与旧失败快照

- id: 2026-11-12-本地阶段已跑完的批次监督台账停在-scheduled-与旧失败快照
- status: fixed
- batch: 05/07 七上人教版课时A计划 Starter Unit 2 Keep Tidy!
- batch_dir: C:\Users\Administrator\.dsh\wf-plugin-data\workflows\07七上人教版课时A计划StarterUnit2KeepTidy!第三课时Sec
- stage: queue
- check: SUPERVISOR_LEDGER_STALE
- created: 2026-11-12T19:31:17+08:00
- last_seen: 2026-11-12T19:31:17+08:00
- occurrences: 1
- fixed_at: 2026-11-12T19:31:17+08:00
- fix_commit: e93ed4d
- verification: workflow.test_supervisor + test_supervisor_scheduling 47 项 OK（新增 test_idle_pause_settles_a_finished_batch_instead_of_leaving_it_scheduled）；真实 05/07 批次 settle 后 status=complete、attempts=0、next_retry_at=0、last_result.action=complete，/api/batches advance.supervisor.status=complete
- title: 本地阶段已跑完的批次监督台账停在 scheduled 与旧失败快照

## 现象（卡点）

format_gate 已 completed、flow_done=true，但 workflow/supervisor.json 仍是 status=scheduled、no_progress_attempts=3，last_result 为「闸门未通过」/「异常已交 AI 运维分析」，工作台持续显示还在排队/待运维

## 具体检查与判定

- 失败检查：SUPERVISOR_LEDGER_STALE
- 判定口径：WorkflowSupervisor.watch 在 _has_work() 为假时直接 pause，不再 one_tick()，因此 _tick_batch 中 local_complete→status=complete 的分支永不执行，台账停留在暂停前最后一次失败派发的快照
- 修复方向：新增 settle() 在空闲暂停前对 local_complete 的批次归一写完成态（清 next_retry_at/retry_reservation_at/no_progress_attempts/escalation，last_result 改为 complete/本地阶段已全部完成），未完成批次不写

## 修复记录

- 修复方式：supervisor._tick_batch 完成分支改为按陈旧字段归一重写；新增 settle() 并在 watch 空闲暂停前调用，只处理 local_complete 批次
- 修复提交：e93ed4d
- 复验证据：workflow.test_supervisor + test_supervisor_scheduling 47 项 OK（新增 test_idle_pause_settles_a_finished_batch_instead_of_leaving_it_scheduled）；真实 05/07 批次 settle 后 status=complete、attempts=0、next_retry_at=0、last_result.action=complete，/api/batches advance.supervisor.status=complete
- 标记时间：2026-11-12T19:31:17+08:00

