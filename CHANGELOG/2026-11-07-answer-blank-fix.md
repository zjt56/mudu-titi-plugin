# 2026-11-07 修复：书面表达无答案导致整卷体检报 ANSWER_INPUT_ERROR

## 背景 / 触发
用户截图：本地自动体检提示
`答案 check_answers：{"passed": false, "issues": [{"code": "ANSWER_INPUT_ERROR", "message": "答案不能为空"}]}`，
同时提示"上游未完成：underlines"，问为什么还有报错。

## 定位（19 年安徽批次的真实数据）
- `06_batch/batch_meta.json` 的 `q-96`（书面表达）`answer` 为空字符串；
  `01_source_ans/answers.json` 中本来就没有第 96 题答案（书面表达原卷无独立答案）。
- `check_answers.collect_json → normalize("")` 抛 `AnswerError("答案不能为空")`，
  `main()` 的 except 把任何异常都包成 `ANSWER_INPUT_ERROR` 并返回 1 →
  一个"书面表达没有答案"被当成整卷输入格式错误，阻断体检。

## 改了什么
- `app/scripts/06-upload/check_answers.py`
  - `normalize`：`None`/空串/纯空白 一律返回 `None`（表示该题没有可核对答案），
    不再抛 `ANSWER_INPUT_ERROR`。
  - `inspect`：`normalized is None` 或值为 占位（无答案/无解析）时跳过，
    既不要求独立答案覆盖，也不做不一致比较。
- 测试对齐：
  - 新增 `MissingAnswerIsNotAnInputErrorTest`（3 条）：空/None/空白归一为 None；
    含书面表达空答案的批次整体通过；纯空白选项答案不再崩。
  - 更新 `test_listening_material_integrity`：空白答案不再 assertRaises，
    改为断言既不报 MISMATCH 也不报 SOURCE_MISSING。

## 结果
- 19 年批次重跑 `checks.run_all`：`passed=True, blocking=[]`（此前卡在 check_answers）。
- 16 年批次仍 `passed=True`。

## 没改什么 / 边界
- 仍坚持"独立答案必须覆盖有答案的题号"：真正缺失的选择题答案依旧报
  `ANSWER_SOURCE_MISSING`（可复核），只是不再把"原卷本就没有答案"当输入错误。
- 未放宽答案一致性/上传/发布边界。

## 如何验证
- 全量 Python **1629 项通过（0 失败 0 错误，17 跳过）**。
- 手工复跑 `checks.run_all`（19 年、16 年）均通过。

## 回退方式
撤销 `check_answers.py` 的 `normalize/inspect` 两处改动即刻恢复旧的"空答案=输入错误"行为。
