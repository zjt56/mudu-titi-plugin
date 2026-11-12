# 2026-11-12 审核闸门卡点修复：课时板块窗口、绑定升级重放、陈旧 unchecked 条目

## 背景 / 触发

批次 `07七上人教版课时A计划StarterUnit2KeepTidy!第三课时Sec` 报「1 处需处理：审核闸门未通过：存在确定性阻断项」，详情是两条
`REVIEW_SAMPLE_SOURCE_UNVERIFIED [sec-1de4ef313aac00fb-u58] / [u52]：scoped 来源窗口或逐字题干未验证`，
另有 15 项人工审核差异（设计态，不是卡点）。排查中又发现三处连带缺陷：修复本身会让已完成的审核轮次被判过期、
`07_review/unchecked.json` 的陈旧行永不出清使闸门恒报必检未过、以及 Windows 上孤儿后端不被复用导致重复实例。

## 改了什么

1. **课时板块尾部裁剪可解释**（`app/workflow/review_child_source.py`）
   新增 `_IMAGE_ONLY_LINE` 与 `_section_end_matches(lines, section, owner)`：`_scoped_context` 不再要求
   `source_section.end_line` 与 `heading_candidates` 重算值严格相等，而是允许「持久化 end 更短、`lesson_container_end_line == end`、
   且 `lines[end:parsed]` 全是空行 / 纯 `[IMG …]` 占位 / 课时或大题标题」的可解释裁剪；裁到真实内容仍判 uncertain。
   批次 05 与 07 的 `_scoped_context` 失败单元从 5/24、6/26 降到 **0/24、0/26**。
2. **绑定升级不重跑模型即可重放**（`review_child_source.py`、`review_rounds.py`、`review.py`）
   `_scoped_context` / `_raw_unit_lines` / `_source_excerpts(_uncached)` / `_excerpt_cache_key` 增加 `strict_end` 形参，
   `_unit_bindings(..., scoped_end=True)` 透传；`acceptable_unit_bindings` 在既有 restore / legacy_source 两轴之外增加
   `scoped_end` 轴，接受「旧解析器定位不到窗口而回退整卷」的历史绑定。效果：存储 plan 的 26 个 `unit_bindings` 全部被接受，
   `evaluate_rounds` 恢复 `execution_completed=True`，不再要求重跑两轮模型。
3. **陈旧分轮 unchecked 条目清理**（`app/workflow/review.py`、`app/workflow/runner.py`）
   新增 `ROUND_UNCHECKED_CODES` 与 `reconcile_unchecked(batch_dir)`：以 `evaluate_rounds` 为权威，仅当 `execution_completed`
   为真时丢弃「code 属该两类 且 `(unit_id, field)` 不在当前 `unchecked` 里」的陈旧行；`do_review_gate` 开头执行并把
   被丢弃行写进 `reconciled_unchecked`。批次 07 复验后 `required_unchecked=0`、`unchecked.json` 为空，只剩 15 项人工差异。
4. **孤儿后端复用**（`lib/index.js`、`backend/backend-lifecycle.test.cjs`）
   看门狗 tick 在 `ensureBackend()` 前先 `probeUnmanagedWorkbench(url)`（`GET /api/health` 且 `workflow_runtime_version`
   为本项目），命中则 `backend.ready = true` 并跳过 spawn，避免 Windows 上 `reapStaleWorkbenchProcesses` 直接 return 0
   导致的重复实例（:8791 / :8792 双后端）。

## 没改什么 / 边界

- 15 项人工审核差异保持 `review_gate=needs_review`，不自动 resolve、不改标签、不重跑该题内容（硬规则 6）。
- 未放宽来源证明：`_section_end_matches` 只接受「板块边界 + 纯占位尾行」，缩到真实题干/材料仍判失败。
- 未自动上传/发布；批次停在 `upload_gate` 等待人工确认。
- 旧批次数据、manifest 完成态、审核结论均未改动。

## 如何验证

- `python -m unittest workflow.test_review_child_source workflow.test_review_local_reuse workflow.test_review_rounds workflow.test_review_source_provenance workflow.test_review_binding_cache workflow.test_operations_autonomous` → **Ran 157 tests, OK**。
- 新增用例：`test_lesson_container_clip_keeps_band_image_outside_the_section`、`test_strict_end_off_replays_the_pre_clip_projection`、
  `test_compat_bindings_replay_the_pre_clip_section_end`；前端 `node --check lib/index.js lib/client.js` 通过，
  `node --test --test-name-pattern "watchdog" backend/backend-lifecycle.test.cjs` 4 通过 0 失败。
- 真实批次 07：`runner run --stage review_gate` → `needs_review`、`execution_completed=true`、`required_unchecked=0`；
  随后 `--stage format_gate` → **completed 19:18:00**，`07_review/upload-format.json` `status=passed`、`errors=[]`、
  78 条 warnings；manifest 收敛为 `pending_confirmation`。

## 回退方式

`git revert` 本提交即可；`reconcile_unchecked` 只在 `execution_completed` 为真时删行，回退后旧 `unchecked.json` 行为恢复。
