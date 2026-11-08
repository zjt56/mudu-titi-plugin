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
