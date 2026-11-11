# 2026-11-11 来源占位清理不再触发重审，旧未裁决差异回到活动队列

## 背景

课时批次 `01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio` 的最终格式检查曾被两类来源占位问题阻断（`ALIAS_CONFLICT` / `YEAR_INVALID` / `SOURCE_TYPE_INVALID`，119 项）。清理占位改的是元数据，但审核的语义绑定把「题目级 source/source_type/year」也算进了单元内容摘要，于是全局 input binding 变化：

1. 已经完整跑完的两轮审核被判为输入过期，重新烧了一遍模型；
2. 旧实现把绑定变了的未裁决发现直接归档出 `07_review/manual-review-queue.json`，用户明确要求继续留着的 11 条差异从活动队列消失（用户 m11332 原话要点：旧 11 条差异要继续 open，不能被技术性变化清掉）。

## 改了什么

- `app/workflow/review.py`：语义投影 `semantic_review_value` / `_unit_bindings` 只在**题目来源元数据**（`source` / `source_type` / `year`）这一层排除，`evidence.source` 等真实来源证据仍参与摘要；新增 `acceptable_unit_bindings(batch_dir, plan=None)` 返回每个单元「可接受的旧摘要集合」：当前投影摘要、旧投影的当前摘要、以及只把当前缺失的已知「未提供」子题来源回放成旧值后的摘要；`_unit_binding_matches` 只在历史摘要精确落在集合内时才认可复用（stem、答案、知识点、真实来源证据任何变化仍然失效）。`_COMPAT_BINDINGS_CACHE` 有界缓存（>64 清空），键含批次/输入摘要/计划摘要。
- `app/workflow/ingest.py`：新增 `RETIRED_BINDING_REASON`、`_historical_unit_bindings`、`_content_equivalent`、`_fresh_finding_id`、`_restore_equivalent_retired`。`build_review_files` 不再一律按「绑定过期」把未裁决差异踢出队列：
  - 只有**同时**满足「退休原因是绑定过期」「仍未裁决」「当前内容与旧摘要等价」三者的记录才回到 `queue` 与 `existing[retired_from]`，并写 `restored_from` / `restored_at`；
  - 内容真变、证明不了、人工已裁决、其他退休原因（旧快照与正式标签不一致等）依旧归档，不恢复；
  - 按 `(finding_id, round_sources)` 与 `(unit_id, field, reason)` 双重去重，重复导入幂等；
  - 恢复项不复用仍活跃的 `finding_id`（真实批次里恢复的 `u73 knowledge` 与活跃的 `u30 knowledge` 都叫 `WF-0015`，撞号会让人工队列两条差异看起来是同一条），改为续 `WF-0021`。
- `app/workbench/server.py::review_snapshot`：卡片的“待人工审核差异”改为统计活动队列里的 open 条目（队列缺失才回退 evaluated findings）。恢复回来的历史差异不在当前两轮 plan 里，只看 evaluated 会显示 12 而队列实际 13 条，数字对不上。新回归 `app/workbench/test_review_snapshot_open_count.py`（2 例）。`n- 新回归 `app/workflow/test_ingest_binding_equivalence.py`（10 例）：等价保留、摘要变化归档、缺历史不假定等价、单元不在计划、旧计划缺该单元、证明抛错回退归档、真实队列保留、恢复 ID 不撞号、恢复两次只留一条。

## 没改什么 / 边界

- 不恢复内容真的变了的差异、不恢复已裁决记录、不恢复其他退休原因；不猜来源、不补题干/答案。
- 未改动任何旧任务、旧响应与在途精确哈希；没有手写 `manifest.json`，队列改动只发生在正常 `build_review_files` 入口。
- 审核差异不裁决、不上传：`review_gate` 仍是 `needs_review`，`upload_gate` / `upload` / `publish_gate` 仍 pending。
- `config/model-router.json`（设备本地）与未知 `.dsh-office-*` 目录不提交。

## 验证

- `python -m unittest workflow.test_ingest_binding_equivalence workflow.test_review_local_reuse` → **25 个用例 OK（30.9s）**（隔离 TEMP `%TEMP%\tiku-ingest-eq`）。
- 真实批次（空间副本预演后执行）：
  - `ingest.build_review_files` 报告 `{'content': 0, 'metadata': 0, 'queue': 13, 'blocking': 13, 'added': 0, 'kept': 12}`，活动队列 **12 → 13** = 11 条旧差异 + 2 条新差异，13 个 `finding_id` 全不重复；二次运行仍 13（幂等）。
  - 正常 `runner run --stage review --force` → `review_rounds: complete, model_calls=0`（复用既有两轮结论，无新模型调用）。
  - 正常 `runner run --stage review_gate` → `needs_review`，13 项差异待人工审核（未放行、未上传）。
  - 正常 `runner run --stage format_gate` → `upload-format.json`: `passed=true, errors=[], deferred=2(CATALOG_REQUIRED / REMOTE_MEDIA_AND_FREEZE_VERIFICATION), warnings=81`，27 units / 41 questions / 7 attachments。
  - 恢复项 `sec-a62dcefde4641bd9-u73 knowledge` 的原始判据与 `round_sources` 完整保留，人工队列中原有 21 条已裁决记录不动。

## 回退

回退 `app/workflow/review.py`、`app/workflow/ingest.py` 与新增测试即可；真实批次若需回到恢复前状态，用 `%TEMP%\tiku-ingest-eq\evidence\before-*.json` 覆盖 `07_review` 的 queue/content/metadata/retired/unchecked 五个文件；归档副本仍留在 `07_review/retired-round-findings.json`，不丢证。
