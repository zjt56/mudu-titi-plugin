# 2026-11-11 原卷合法空/图形态约束（代码规则+提示词硬化，防同类误判复发）

## 背景

用户要求把已出现的两次同类卡点（结构闸门误判无题号无图片题组、前置组装把空白作答网格/纯图片题当内容缺失）
沉淀为通用约束，避免下次同类或类似错误。此前两处各自临时修补，口径分散、易漂移。

## 改了什么

- `app/workflow/question_content.py`：新增统一分类 `legitimate_source_shape(unit, appendix_text)`，返回
  `image_only`（纯图片题）/`blank_answer_grid`（空白作答网格，答案在卷末附录可按题号对上）/
  `unnumbered_no_image`（整节无印刷小题号也无图片但有正文）之一，否则 None；`capability_limited_source`
  为其布尔封装。`LEGITIMATE_SHAPES` 显式声明三类。
- `app/workflow/structure_gate.py`：`_build_scoped_units` 的题组无题号窗口判定复用同一分类口径（注释标注）。
- `app/workflow/agent_tasks.py`：`types` 校验与 `accept_result` findings 降级继续走 `capability_limited_source`；
  内置英文指令 `RULES["types"]` 明确列出三类合法形态不算缺失、只在真实截断/缺选项/错配时填 false。
- `app/prompts/roles/preassemble.md`、`app/prompts/roles/types.md`：脑子检查新增“先排除三类合法空/图形态再判
  contentComplete=false，并写明真实缺了什么文字”。
- `AGENTS.md` 硬规则9、`CHANGELOG/RULES.md`：新增“原卷合法空/图形态不算内容缺失”条，指向统一分类函数。
- 测试：`test_question_content.LegitimateSourceShapeTest`（6 条，覆盖三类+反向真实缺失/纯文本）；
  `test_agent_roles` 新增 2 条——提示词与内置指令必须声明三类且与 `LEGITIMATE_SHAPES` 同步。

## 没改什么 / 边界

- 不放松真实内容校验：题干截断、缺选项、题干错配、表格非空但缺字、附录无对应答案仍按原逻辑阻断。
- 不改题组边界、题号唯一绑定、来源窗口，不改审核/上传口径，不自动上传发布。
- 能力受限只作 info/记录，不据此伪造内容或改写题目。

## 验证

- `test_question_content`、`test_agent_roles`、`test_agent_tasks`、`test_structure_gate`、`test_agent_prepare`、
  `test_preassembly` 共 120 项通过（4 skipped）。
- 真实批次 preassemble 响应重校验仍 completed=true；先前 preassemble 已推进到 enriching 不受影响。
- 语句漂移防护：`test_agent_roles` 断言三类名称与 `LEGITIMATE_SHAPES` 一致、提示词/内置指令含英文关键词。

## 回退

回退本提交即可恢复“各处各自判定”的旧形态；不会改动用户批次数据。真正的三类合法形态将重新可能被误判阻断。
