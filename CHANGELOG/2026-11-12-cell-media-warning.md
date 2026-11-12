# 表格单元格内图片：能力受限降为显式提示，不再阻断最终格式闸门

## 背景（触发）
批次 `05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec` 在 2026-11-12 17:22 完成
`review`（`review_gate=needs_review`，20 条真实差异待人工判断）后，`format_gate` 以 3 条
`LAYOUT_CELL_MEDIA_UNSUPPORTED` 确定性失败，批次整体 `failed`，无法 prepare-upload：

- 出错单元 `sec-242483090077723c-u121`（阅读理解），`[TABLE 4]` 三行首格原文是
  `[IMG image18.jpeg]<br>Alice` 这类单元格内图片；
- 远端契约（`http://101.43.1.57/docs/`）`materialBlocks` 的单元格只接受
  `text/items/rowspan/colspan/header`，attachments 只能绑定题目/选项/答案/解析，
  **平台确实没有"单元格级附件"位置**；
- 图片本身已按单元级 `placement:"question"` 交付（本批 4 个附件），单元格文字保持原文。

用户明确选择：**降为显式 warning，继续跑到 upload_gate**，不丢图、不伪造成功。

## 改了什么
- `app/workflow/source_objects.py`
  - 新增 `NON_BLOCKING_CAPABILITY_CODES = {"LAYOUT_CELL_MEDIA_UNSUPPORTED"}`；
  - `upload_errors()` 改为 `layout_issues()` 的薄封装（只返回阻断错误，保持旧调用语义）；
  - 新增 `layout_issues(batch_dir, items) -> {"errors": [...], "warnings": [...]}`，
    把内部重建逻辑集中到 `_layout_scan()`：单元格媒体限制进 `warnings`（带
    `non_blocking: true`、`unit_id`、`row`、`column`，消息补充"图片已按单元级附件随题交付，
    表格文字保持原文"）；嵌套表格、来源绑定不成立、投影不一致等结构性错误仍进 `errors`。
- `app/workflow/upload_payload.py`：`build_payload` 改用 `layout_issues()`，
  errors 与 warnings 分别并入 `builder.errors` / `builder.warnings`。

## 没改什么 / 边界
- `LAYOUT_NESTED_TABLE_UNSUPPORTED`、`LAYOUT_SOURCE_BINDING_UNPROVEN`、
  `LAYOUT_TABLE_PROJECTION_MISMATCH/INVALID`、`LAYOUT_SOURCE_EVIDENCE_INVALID` 仍阻断。
- 不改批次数据、`materialBlocks`、附件归属、审核队列与审核结论；不自动上传/发布。
- 不把能力限制伪装成"通过"：错误码、单元、行、列与原因都留在
  `07_review/upload-format.json.warnings`，`review_gate` 仍为 `needs_review`。

## 如何验证
- `PYTHONPATH=app python -m unittest workflow.test_source_objects`：18 项 OK；
  新增/改写两条用例——`test_cell_media_limitation_is_visible_warning_not_a_gate_failure`
  （payload errors 无 `LAYOUT_*`、warnings 含该码且 `non_blocking`）、
  `test_nested_table_capability_still_blocks_the_final_gate`（嵌套表格仍报错）。
- `PYTHONPATH=app python -m unittest workflow.test_upload_payload workflow.test_format_gate`：95 项 OK。
- 真实批次复跑：`python -m workflow.runner run --batch-dir <批次> --stage format_gate`
  → 2026-11-12T17:38:26 `format_gate` **completed**；`07_review/upload-format.json`
  `status=passed`、`passed=true`、`errors=[]`，3 条 `LAYOUT_CELL_MEDIA_UNSUPPORTED`
  出现在 `warnings`（`non_blocking=true`）；manifest `status=pending_confirmation`，
  下一步停在 `upload_gate`（人工，本地不自动上传）。

## 回退
回退本文件提到的两处代码改动即可恢复"单元格媒体阻断"口径；批次报告会在下次
`format_gate` 运行时按旧口径重新生成，不改历史题目内容。

## 关联
- ISSUES/fixed：`2026-11-12-格式闸门确定性失败-表格单元格内图片平台契约无法表达.md`。
