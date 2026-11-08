# 2026-11-08 格式小问题不再阻断流程

## 背景 / 触发

用户反馈 2024 卷卡在：

```
本地自动体检：阶段失败
原因：画线 check_underline：[E19] 书面表达题干含连续下划线: q-81
     （_____ 应删除，答题线由前端画）
怎么办：确定性检查未过：按上面列出的阻断项修复对应问题后重跑该阶段。
```

用户明确：**“标点符号呀、下划线呀，这些在流程通过性上都是小问题，只要不是大错误就行。
只有那种题目、题干、题号对不上的才是大问题。”**

## 改了什么

1. `app/scripts/_common.py`
   - `report(problems, ok_msg, soft_default=False)`：以 `[W` / `[INFO]` / `[提示]` /
     `[PREF]` 开头的行是**软提示**，只打印 `[WARN]`，退出码仍为 0；
     `soft_default=True` 时该检查全部 findings 都只提示（用于纯格式检查）。
2. 检查脚本降级（只提示，不阻断）：
   - `scripts/06-upload/check_underline.py`：`[E19]`→`[W19]`（书面表达答题线占位、
     连续下划线），`[E16]` 空位写法→`[W16]`。真正的“原文该画线却缺失”
     （`UNDERLINE_MAPPING_MISSING`、问句指向的画线词缺失）仍为 `[E16]` 阻断。
   - `scripts/06-upload/check_tables.py`：列数不一致、合并单元格重叠、全角空档、
     空档含空格、信息转换 variant/子题号 → `[W…]`。
   - `scripts/06-upload/check_text.py`：`U+3000` 空档、`<br>`、首字母与空档间距 → `[W]`。
   - `scripts/06-upload/audit_sentence_blanks.py`：完成句子空档写法 → `soft_default=True`。
3. `app/workflow/checks.py`：`画线 check_underline`、`空档 audit_underlines`、
   `表格 check_tables`、`完成句子 audit_sentence_blanks` 设为 `blocking=False` 兜底，
   即使脚本退出码非 0 也不阻断体检。
4. `app/workflow/diagnose.py`：为这几个检查补上“属格式提示，不阻断”的解释，
   避免下次再出现“按阻断项修复”这种误导性提示。
5. 根 `AGENTS.md` 硬规则 9 补充该口径；`CHANGELOG/RULES.md` 记录用户原话。

## 没改什么 / 边界

- **实质错误仍然阻断**：题号缺失/重复、题干或选项与原文不符、答案不符、
  来源审计失败、结构错位、AI 生成内容、标签非法等全部保持阻断。
- 不修改任何题目数据；2024 卷只重跑 `validate` 使其通过，未改原卷/答案/标签。

## 如何验证

- `workflow.test_validation_contracts` 中相关断言改为“格式提示、退出码 0、
  输出含 `[W`”，并保留“原文画线缺失仍阻断”的用例。
- `workflow.test_explanation_gaps`、`test_source_completion_scripts`、
  `test_upload_content`、`test_no_ai_generation_rule`、
  `test_validation_contracts`：**98 项通过**。
- 2024 卷真实批次：`runner validate` 由失败转为**通过**（15 项检查全绿）。

## 回退

还原 `_common.report` 与四个脚本的问题前缀、`checks.py` 的 blocking 标记即可。
不要回退 `AGENTS.md` 的规则说明（用户明确要求）。

## 全链路复核（2026-11-08 追加）

按“不能只改一处”的要求，对全部 7 个批次做了只读复核：

| 批次 | validate（真实判定） | 说明 |
|---|---|---|
| 14 年、16 年、22 年、23 年、2024 | **通过** | 仅剩 5 项格式软提示（排版/空档/画线/表格/完成句子） |
| 18 年、19 年 | **仍阻断** | `标签 check_tags` + `目录与匹配规则 label_audit`：听力关键词语选择残留
旧「语音/基本读音」知识点（2026-11-08 15:00 已改映射，需重跑 knowledge 才消失） |

后续链路（未受本次改动放宽）：

- `review` / `review_gate`：各批次均按“有差异才待审”产出人工队列（11–20 项），闸门如实
  `needs_review`；不存在“格式提示变通过后闸门跟着放行”的路径（闸门读的是审核差异，不是格式检查）。
- `format_gate`：14/16/22/23/2024 均 `passed=true`、`errors=0`；2024 重跑 validate 后已生成报告。
- `upload_gate/upload/publish_gate`：本地一律 skipped/人工，未自动上传。

另外补了一处“不能绕过”的加固：`checks.run_all` 直接调用时若缺 `source`，
需要原文的检查会被跳过，过去会因“没报错”而显示通过；现改为显式
`SOURCE_INPUT_MISSING` 阻断，避免单项体检静默放行。`runner.do_validate` 始终带 source，
实际流程不受影响（146 项回归通过）。

结论：本次只放宽“格式表达”一类，题号/题干/答案/来源/结构/标签等实质阻断全部保留；
18/19 年的阻断是既有的标签口径问题，不是本次改动引入。
