# 卡点：运维租约被并行后端互毁，每轮 claim 后被 process_replaced 打回

- id: 2026-11-11-运维租约被并行后端互毁,每轮-claim-后被-process_replaced-打回
- status: fixed
- batch: 【木牍教育】12年安徽中考英语
- stage: operations
- check: OPERATIONS_LEASE_PROCESS_REPLACED
- created: 2026-11-11T13:05:41+08:00
- last_seen: 2026-11-11T13:05:41+08:00
- occurrences: 1
- fixed_at: 2026-11-11T13:27:40+08:00
- fix_commit: 3480b1b
- verification: workflow.test_operations+workbench.test_health_busy 84项通过；宿主 operations-agent/ui/http 67项通过；现场 op.leased=false 且不再出现新的 process_replaced
- title: 运维租约被并行后端互毁，每轮 claim 后被 process_replaced 打回

## 现象（卡点）

AI运维面板反复出现 worker_busy: 批次执行锁已占用; name=Error。operations.json 在 12:02:40-12:09:44 七分钟内产生 22 次 lease/process_replaced + 21 次 claim/claimed、0 次完成；incident.status 在 claimed/queued 间抖动，execution_process.pid 反复指向已退出进程（34224/34912/7132）。

## 具体检查与判定

- 失败检查：OPERATIONS_LEASE_PROCESS_REPLACED
- 判定口径：判定口径缺陷：_expire() 只要 execution_process 与本进程不同就把 claimed 打回 queued 并记 process_replaced，未区分「原进程已死」与「另一活着的后端实例持有效租约」。同一数据根上 8791/8792 双后端时双方互毁租约，任一轮都跑不完。另宿主主进程 9328 自 2026-11-09 20:30 未重启，lib/index.js 的 409 准入映射未加载，409 被渲染成裸 Error（name=Error 无 code）。
- 修复方向：已修：operations.py 新增 _pid_alive/_foreign_owner/leased，_expire 仅在租约到期或原进程消失时释放，claim 遇存活并行持有者抛 OperationsBusy（worker_busy: 另一后端实例正在处理该批次）；server.py 的 work_in_flight 纳入运维租约使 /api/health.busy=true 以延后热重启；宿主整进程重启以加载新插件 JS。

## 修复记录

- 修复方式：租约只在到期或原进程消失时释放；并行后端命中持租约抛 worker_busy 走 409 等待；health.busy 计入在途运维租约以延后热重启
- 修复提交：3480b1b
- 复验证据：workflow.test_operations+workbench.test_health_busy 84项通过；宿主 operations-agent/ui/http 67项通过；现场 op.leased=false 且不再出现新的 process_replaced
- 标记时间：2026-11-11T13:27:40+08:00

