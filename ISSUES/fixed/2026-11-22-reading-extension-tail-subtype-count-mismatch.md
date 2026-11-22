# 卡点：阅读理解含 ☆拓展设问 时「选择4题」被误判 SUBQUESTION_COUNT_MISMATCH 及运维恢复 ImportError

- id: 2026-11-22-reading-extension-tail-subtype-count-mismatch
- status: fixed
- batch: 1126秋七上人教版课时A计划StarterUnit3Welcome!第三课时S
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/1126秋七上人教版课时A计划StarterUnit3Welcome!第三课时S
- stage: format_gate / operations
- check: SUBQUESTION_COUNT_MISMATCH & operations.py ImportError
- created: 2026-11-22T10:03:24+08:00
- last_seen: 2026-11-22T10:25:26+08:00
- fixed_at: 2026-11-22T10:28:10+08:00
- fix_commit: working-tree
- title: 阅读理解含 ☆拓展设问 时「选择4题」被误判 SUBQUESTION_COUNT_MISMATCH 及运维恢复 ImportError

## 现象与口径确认

1. 批次 `1126秋七上人教版课时A计划StarterUnit3Welcome!第三课时S` 的 `sec-1d6f85c14b40fd62-u106` 为 3 道选择 + 1 道 `☆拓展设问`（共 1、2、3、4 四小题），标为 `选择4题`，此前被 `upload_payload.py` 强制排除拓展设问后报 `SUBQUESTION_COUNT_MISMATCH`。
2. 用户明确口径：`☆拓展设问` 属于该题组的正式小题（1、2、3、4 同属本题型），`选择4题` 应通过。
3. 运维恢复时 `operations.py:1803` 执行 `from . import preassemble` 抛出 `ImportError` 导致流程卡住。

## 修复记录

- 修改 `app/workflow/upload_payload.py`、`app/workflow/agent_tasks.py`、`app/workflow/sync_practice.py`、`app/workflow/question_content.py`、`app/workflow/operations.py`、`app/workflow/self_heal.py`、`app/prompts/roles/{preassemble,types}.md`，并更新回归测试。
- 复验证据：`test_sync_practice`、`test_question_content`、`test_upload_payload`、`test_question_draft` **170/170 通过（1.036s，4 skipped）**；`/tmp` 副本 `upload_payload.build_payload` 不再报 `SUBQUESTION_COUNT_MISMATCH`。
