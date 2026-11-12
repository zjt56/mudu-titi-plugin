# 卡点：knowledge 局部重判被拒后 supervisor 无限重派，反复拉起 headless 进程致整机卡死

- id: 2026-11-11-knowledge-局部重判被拒后-supervisor-无限重派,反复拉起-headless
- status: fixed
- batch: 03七上人教版课时A计划StarterUnit1Hello!第三课时Section
- batch_dir: workflows/03七上人教版课时A计划StarterUnit1Hello!第三课时Sectio
- stage: knowledge
- check: ValueError
- created: 2026-11-11T21:30:44+08:00
- last_seen: 2026-11-11T21:30:44+08:00
- occurrences: 1
- fixed_at: 2026-11-11T21:41:05+08:00
- fix_commit: uncommitted-working-tree
- verification: PYTHONPATH=app python3 -m unittest workflow.test_self_heal ... test_agent_tasks => Ran 166 tests OK；新增回归 test_volatile_detail_does_not_reset_retry_budget 通过；现场模拟同预算 key attempts 1→2→3 第3次 retry_budget_exhausted
- title: knowledge 局部重判被拒后 supervisor 无限重派，反复拉起 headless 进程致整机卡死

## 现象（卡点）

self-heal 对 unit sec-0ed7068246aa3d4e-u21（第5题完成句子）标签「语用/情景交际」超出允许维度(词汇/语法) 进行 rejudge_knowledge，结果 status=refused/verified=false；supervisor.json 同时处于 status=scheduled 且 next_retry_at 已过期，每轮重新派发整个阶段，每轮 fork 多个 dsh headless(~500MB/60%CPU) 并发，load avg 冲到 45，内存打满，电脑两次卡死。

## 具体检查与判定

- 失败检查：ValueError
- 判定口径：局部重判被明确拒绝(refused)属确定性失败，但 supervisor 仍无限重派同一工作；part_concurrency=16 + PARALLEL_AGENTS_ENABLED 使每轮派发瞬间拉起十几个 headless 进程，超过本机资源上限。
- 修复方向：1) 将 local-agent.json part_concurrency/review_part_parallel 从16降到3；2) 停止该批次 supervisor 自动重派并清掉 scheduled/next_retry 预约，避免同输入空转；3) 该超纲标签应受限重判或按允许维度纠正，不得靠重试消化。

## 修复记录

- 修复方式：根因修复：self_heal.recover 的重试预算 signature 不再拼易变 detail，改用 _failure_class_key 归一化失败类别（抹除 unit id/exit code/时间戳/行号/内嵌 JSON），使同一确定性失败跨轮共享预算，达到 MAX_ATTEMPTS_PER_STAGE 后进入 retry_budget_exhausted 长冷却，不再无限整阶段重跑拉起海量 headless；local-agent.json 并发 16→3。
- 修复提交：uncommitted-working-tree
- 复验证据：PYTHONPATH=app python3 -m unittest workflow.test_self_heal ... test_agent_tasks => Ran 166 tests OK；新增回归 test_volatile_detail_does_not_reset_retry_budget 通过；现场模拟同预算 key attempts 1→2→3 第3次 retry_budget_exhausted
- 标记时间：2026-11-11T21:41:05+08:00

