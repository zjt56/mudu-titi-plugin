# 全量“无答案”决定不再被判为答案输入格式错误（批次03）

## 背景/触发

批次 `03八上人教版课时A计划Unit1HappyHoliday第三课时Grammar` 卡在本地自动体检：

```
答案 check_answers：{"passed": false, "issues": [{"code": "ANSWER_INPUT_ERROR",
"message": "答案 JSON 需要题号 map、answers 或 number/answer 数组"}]}
```

该批次来源是 PDF，正文/附录里**确实没有答案区**（`01_source/附录.txt` 为空，`pdf-ocr.md`
全文检索「答案/参考」0 次，末尾停在 Ⅲ.完成句子第 5 题）。答案阶段因此按规则产出了
“全部无答案”决定：`answers={}`、`evidence={}`、`missing` 覆盖每个确认题号
（`未提供答案原文；按无答案占位继续`），批次 24 个单元的答案字段是 `无答案` 占位。

`check_answers.collect_json` 走进 `elif "answers" in value:` 后递归到空 dict，直接抛
`答案 JSON 需要题号 map、answers 或 number/answer 数组`，于是把“原卷确实没有答案”
误报成答案文件格式故障，整卷体检失败。

## 改了什么

- `app/scripts/06-upload/check_answers.py`：`collect_json()` 在最前面识别**正式无答案决定**
  （`answers` 为空且 `missing` 为非空 dict）→ 直接返回空答案集，交由 `inspect()` 按
  “无独立答案可比、空白答案不算缺失”判定；
  `answers` 为空且**没有** `missing` 证据时仍抛原来的格式错误，不静默放行。
- `app/workflow/test_source_completion_scripts.py`：新增
  `test_all_missing_decision_artifact_is_not_an_input_error`（同时断言无 missing 时仍报错）。

## 没改什么 / 边界

- 没有放宽“有答案就必须逐题核对”的口径：只要有真实独立答案，仍旧逐题比对并在缺失/不一致时阻断；
- 没有把 `无答案` 占位改写成任何内容，也不生成答案（规则22：不编造）；
- 不改变答案阶段的产物结构，只让消费者能正确读取既有结构。

## 如何验证

- 批次 03 直接复跑单项：`check_answers --batch 06_batch/batch.json --answers 01_source_ans/answers.json`
  → `passed: true`、`issues: []`、覆盖 32 个 question ID；
- `python -m workflow.runner validate --batch-dir …` → **本地体检通过**（13 项检查全 ✓）；
- 回归 `workflow.test_source_completion_scripts` → **20 项通过**（含新增用例）。

## 回退方式

删除 `collect_json()` 顶部的无答案决定分支即可恢复原行为（会让此类批次重新判为输入错误）。
