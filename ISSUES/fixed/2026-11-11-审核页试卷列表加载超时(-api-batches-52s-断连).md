# 卡点：审核页试卷列表加载超时（/api/batches 52s 断连）

- id: 2026-11-11-审核页试卷列表加载超时(-api-batches-52s-断连)
- status: fixed
- batch: 01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio (全局批次列表)
- batch_dir: C:\Users\Administrator\.dsh\wf-plugin-data\workflows
- stage: 工作台/列表
- check: BATCH_LIST_PROJECTION_SLOW
- created: 2026-11-11T18:42:55+08:00
- last_seen: 2026-11-11T18:42:55+08:00
- occurrences: 1
- fixed_at: 2026-11-11T18:45:34+08:00
- fix_commit: eb34ccf
- verification: 13 批串行冷投影 68s→8.88s，命中快照 3.40s；真实 GET /api/batches 200/1.03-1.46s、13 根 URL 批次、课时批次 open=13；workflow.test_review_rounds+test_review_local_reuse+test_review_child_import+test_ingest_binding_equivalence+test_review_binding_cache 115 tests OK；workbench.test_overview_snapshot 3 OK
- title: 审核页试卷列表加载超时（/api/batches 52s 断连）

## 现象（卡点）

GET /api/batches 52.4s 后 基础连接已经关闭: 接收时发生错误；前端 jget 15s 超时提示 请求超过15秒

## 具体检查与判定

- 失败检查：BATCH_LIST_PROJECTION_SLOW
- 判定口径：13 个批次串行冷投影 68s：review.acceptable_unit_bindings 每单元每次现算全部输入摘要（单批 480 次、31552 次 Path.resolve/16078 次 _file_digest），_confirmed_review_units 反复深拷贝，review_rounds._source_excerpts 每单元扫全文 1677 次；重启后无任何缓存
- 修复方向：缓存键改用计划摘要、单元解析按 confirmed 指纹缓存、_payload_reusable_checks 每记录只算一次接受集合、摘录按 sha256(source)+单元内容记忆、列表投影落进程外快照并指纹校验后复用、启动预热

## 修复记录

- 修复方式：review.acceptable_unit_bindings 缓存键改用计划摘要（计划已含 input_hashes）、单元解析按 structure.confirmed.json 指纹缓存、_payload_reusable_checks 每条记录只算一次接受集合、review_rounds._source_excerpts 按 sha256(source)+单元内容记忆、批次列表投影落 %LOCALAPPDATA%/md-upload-tiku/overview-snapshot.json 并仅在指纹一致时复用、启动预热；_plan_input_key 保持纯内容哈希
- 修复提交：eb34ccf
- 复验证据：13 批串行冷投影 68s→8.88s，命中快照 3.40s；真实 GET /api/batches 200/1.03-1.46s、13 根 URL 批次、课时批次 open=13；workflow.test_review_rounds+test_review_local_reuse+test_review_child_import+test_ingest_binding_equivalence+test_review_binding_cache 115 tests OK；workbench.test_overview_snapshot 3 OK
- 标记时间：2026-11-11T18:45:34+08:00
