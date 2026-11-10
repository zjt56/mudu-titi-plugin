# 2026-11-10 修复：文章大意的题组归属与解析行被误判为“两个题号”

## 背景

批次【木牍教育】21年安徽中考英语 在「打标签：解析与文章大意」和「本地自动体检」连续卡住：

```
group-59-60-61-62: SUMMARY_SOURCE_UNVERIFIED 文章大意未能唯一逐字绑定原材料
group-59-60-61-62/59: EXPLANATION_SOURCE_UNVERIFIED 解析未能唯一逐字绑定原题
group-73-74-75: SUMMARY_SOURCE_UNVERIFIED
```

这些内容其实都在 `01_source/附录.txt` 里逐字存在（第 63、98 行的大意；第 64 行起的解析），不是模型编造。

## 根因（两处解析 bug）

1. `upload_content._question()` 用“同一行里还有 `\d+.` 就是第二个题号”判断歧义。第 64 行
   `59.A　【解析】…at age 5.”可知…` 里的 `age 5.` 被误当成第二个题号，导致 **Q59 整行被丢弃**。
2. 大意归属扫描遇到 `QUESTION_BOUNDARY`（`59.`）就 `break`，因为 Q59 已被丢弃且该行没有正常解析标签，
   于是 `【文章大意】` 的 `owner=None / members=[]`。体检里 `row['owner'] in numbers` 永远不成立，就报
   `SUMMARY_SOURCE_UNVERIFIED`。同样的问题出现在“答案行与【解析】分行”的题组（73–75），
   这些纯答案行能进 `members`，但 `owner` 仍是 None。

## 改了什么

- `app/workflow/upload_content.py::_question`：只有**题号大于当前题号**、且后面紧跟选项字母
  （`A–D`）或 `【解析】` 的内联编号才算“同一行第二个题号”。`age 5.`、`page 3.` 这类句中数字不再误判。
- `_question` 丢弃行的问题消失后，`【文章大意】` 向前能找到首个题号（59 / 73）。
- 大意归属增加兜底：若正向扫描已经收集到 `members` 但 `owner` 仍为空，直接取 `members[0]`，
  覆盖“纯答案行 + 下一行【解析】”的真实附录版式。
- `test_upload_content.py` 新增两条回归：
  - “解析里含 `at age 5.`”仍能识别该题并把大意归属到该组；
  - “纯答案行 + 下一段【解析】”仍能归属题组。

## 边界

- 仍要求大意/解析逐字绑定权威原文（`01_source/附录.txt` / Word 正文）；改写、编造仍拒绝。
- 只有“第二个题号大于当前题号且带选项/解析标记”才算歧义，真正一行多题仍会被判定歧义而不误绑定。
- 不改任何批次已有产物；修复后重新导出/重跑该阶段即可通过。

## 验证

- 21年安徽卷：`restore_source_content` 对现有 `explanations.json` 返回 `passed=True`，errors 为空；
  大意行归属为 `line 63 → owner 59, members [59,60,61,62]`、`line 98 → owner 73, members [73,74,75]`。
- `workflow.test_upload_content`：49 项通过（含 2 条新回归，原有“一行多题判歧义”用例保持通过）。
- `test_upload_content + test_explanations_direct + test_explanation_gaps + test_agent_tasks + test_self_heal_gaps + test_validation_contracts + test_assemble_precheck`：164 项通过。

## 回退

回退 `upload_content._question` 的歧义判断与大意 owner 兜底，并删除两条回归测试；行为回到误判。
