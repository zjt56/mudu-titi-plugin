# 2026-11-11 完形/补全对话的编号空格与空位号不再被误判为重复题号/漏题

## 背景

批次「03七上人教版课时A计划StarterUnit1Hello!第三课时Sectio」结构自动确认被阻断，主因为大量
NUMBERING_RESTART 及 SOURCE_DUPLICATE_NUMBERS/QUESTION_NOT_FOUND。核对原卷：
- 完形填空材料行内 `<u>　1　</u>…<u>　10　</u>` 是编号空格（子题号本体），其 `</u>.` 后的句点让
  “数字+句点”锚点正则命中 1/9，与下方 `( )1.` 选项行重复，触发 SOURCE_DUPLICATE_NUMBERS 与 QUESTION_NOT_FOUND。
- 补全对话空位写成 `A:Hello,I am Tony.1.<u>　　</u>`（题号在空位前、紧跟文本句点），`_weak_numbers` 只认
  空位在数字前的写法，定位不到第1题空位，触发 QUESTION_NOT_FOUND。

## 改了什么

- `app/workflow/structure_gate.py::_scope_anchors`：先按库侧空档写法 `normalize_blank_text` 规范化每行再扫
  印刷题号锚点，使 `<u>　n　</u>`→`__n__` 不再被“数字+句点”误当题号；weak 锚点也基于规范化行。
- `app/workflow/preview.py::_weak_numbers`：新增识别“题号在空位前”的补全对话/信息转换空位，如
  `2.　　`、`Tony.1.____`（`(?<!\d)N[.．]\s*(?=空档标记)`），并要求其后确有 `　`/`__`/`_` 空档，避免吞普通句号数字。
- 回归：`test_structure_gate` 新增 2 条——完形材料编号空格不作印刷锚点（锚点集合唯一）；含内联编号空格的
  完形组在 scoped 闸门下不报 SOURCE_DUPLICATE_NUMBERS/QUESTION_NOT_FOUND。

## 没改什么 / 边界

- 未放宽题号唯一绑定：真实同窗重复题号、跨材料、缺失题号仍阻断；仅修正“编号空格/空位号被当印刷题号”的误判。
- 不改题组边界、来源窗口、审核/上传口径；不改用户数据。

## 验证

- `test_structure_gate` 15 项通过；`test_question_content`、`test_question_identity*`、`test_sync_practice`、
  `test_agent_tasks` 等合计 153 项通过（4 skipped）。
- `test_preview_labels`、`test_question_identity_{checks,pipeline,preview,answers,format}`、
  `test_sync_lesson_shape`、`test_sync_source_marks` 合计 73 项通过。
- 真实批次 03 结构闸门重跑：25 单元（独立22/组合3），9 条 NUMBERING_RESTART/GROUP_BOUNDARY_REVIEW 全部转
  “每个原题位置已唯一绑定”通过；批次进入 classifying，structure_gate=completed。

## 回退

回退本提交 `structure_gate/_scope_anchors` 与 `preview/_weak_numbers` 及两条测试；该批次将重新因编号空格/空位号误判阻断。
