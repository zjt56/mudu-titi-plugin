# 阅读理解选择N题不把开放式拓展设问计入选项题数

日期：2026-11-13。类型：上传最终格式校验（校验口径修正）。

## 背景与触发

批次 `11七上人教版课时A计划StarterUnit3Welcome!第三课时Sect` 的最终上传格式检查失败：

```
SUBQUESTION_COUNT_MISMATCH [sec-59ddc72436ce2ed0-u75]：子类选择3题与 4 个结构化子题不符
```

该单元原卷是阅读理解 Ⅱ：3 个带括号题号的四选一小题（1 词汇、2 细节多选、3 跨学科设问），
外加 1 个 `☆拓展设问` 后的开放式问答题（4，无选项）。结构和答案都是 4 个小问。

旧口径把「选择N题」直接按组内全部子题计数：`len(children) != N` 即报错，于是固定写成
`选择3题`（四选一题数）的单元只要再带一个拓展设问就会互相矛盾。

## 改了什么

- `app/workflow/upload_payload.py`：新增 `_extension_child(unit, child)` 判定——
  子题干含 `☆拓展设问` 标记，或该组其它子题都有选项而它是唯一无选项子题，即视为开放式拓展追问；
  「选择N题」计数时排除它，但**题组仍保留该子题**。两个及以上无选项子题不构成唯一尾题，仍判不一致。
- `app/workflow/test_upload_payload.py`：新增回归——带标记/不带标记的单一拓展尾题均通过且 4 个子题保留出口径；
  两个无选项子题仍触发 `SUBQUESTION_COUNT_MISMATCH`。

## 没改什么 / 边界

- 不修改任何原卷数据、结构、答案或审核结论；不把拓展设问改写成选题、也不删除；
- 仍严格要求题组 `numbers` 与子题号完整一致、选项数量与子类一致；
- 只让「选择N题」按选项题计数；`N空`（完形/短文等填空组）不受影响。

## 验证

- `workflow.test_upload_payload`：72 项通过；
- `test_upload_payload` + `test_format_gate` + `test_stage_semantics` + `test_upload_numbering`：144 项通过；
- 真实批次：`format_gate` 由 `failed` 变 `completed`，`upload-format.json` `passed=true, errors=[]`；
  该单元在 `python3.12`（格式闸门解释器）下复验不再报错。

## 回退

撤回本提交即可恢复旧的严格计数；无需改动任何批次数据。
