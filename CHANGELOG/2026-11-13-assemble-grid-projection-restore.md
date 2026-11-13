# 装配来源审计与回填共用同一版面投影（网格题不再被改回错位内容）

- 日期：2026-11-13
- 触发：批次「七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun」在
  `ocr_grid` 修好草稿图文绑定之后，装配仍连续失败两次：
  先 `Assemble source audit failed: [BATCH_IDENTITY_MISMATCH … u9/u24/u77/u120]`，
  再 `Assemble source audit failed: [SOURCE_STEM_MISSING … u24 第 4 题题干缺失或改写]`。

## 背景

1. **确认结构落后于当前注释代码**：`01_source/structure.confirmed.json`（18:27 发布）里
   `source_section.column_path` 为 `null`，而当前 `lesson_bands`/`visual_columns` 产出的重算单元带
   `[{column_id: c_A, label: A 基础巩固, role: container}]` 等栏目归属。来源身份校验（`scoped-v1`）
   逐字段比较，因此报 `BATCH_IDENTITY_MISMATCH`。这不是内容差异，而是确认结构没有按当前代码重算。
2. **来源审计与装配回填投影不一致**：`question_content.audit_batch_source` 已经按 `ocr_grid`
   页面坐标投影比较子题（`_grid_source_audit`），但装配阶段 `restore_source_stems` 仍用线性
   `_parts` 回填。双栏网格页的 OCR 行序是行优先的，于是回填把第 2/4/5 格改成相邻题内容，
   第 4 格只剩一张图片，审计随即判 `SOURCE_STEM_MISSING`——**审计是对的，回填在破坏正确结果**。

## 改了什么

1. `app/workflow/question_content.py`
   - 新增 `_grid_source_audit(root, unit, audit)`：`ocr_grid.source_audit_items()` 可用时，把审计的
     每格题干替换为网格投影的 `item_stem()`、选项清空，并去掉 `SOURCE_OPTIONS_INCOMPLETE`
     误报（网格单元格本就不带字母选项）；`audit["issues"]` 重新计算。
   - `restore_source_stems()`：每个确认单元先取一次 `ocr_grid.source_audit_items(root, unit)`；
     逐题命中网格单元格时**直接按网格建立回填项**（即使线性解析根本没找到该题），不再让线性
     结果覆盖网格结果；网格单元格的回填项带 `authoritative=True`。
   - `fill()` 增加：`authoritative` 项没有选项时，清掉目标里由线性错配带来的旧选项
     （图片题曾因此报 E20）。
2. `app/workflow/test_ocr_grid.py`
   - 新增 `GridAwareRestoreTest`：构造网格批次与「线性正文里没有 1./2. 题号锚点」的载荷，
     断言 `restore_source_stems` 按网格写入五格图文、并把线性错位选项清空。
   - 已有 `SectionItemsTest`（6 项）与 `PostfixImageOptionTest`（2 项）保留，共 **9 项**。

## 没改什么 / 边界

- 不重排识别服务输出、不改写 `01_source/正文.txt`、不新增或补造题号/图片名。
- 网格判定不成立（单栏、跨栏、标记未投影、题号集合不符）时，回填行为与原来完全一致。
- **来源审计没有放宽**：`SOURCE_STEM_MISSING`/`SOURCE_OPTIONS_INCOMPLETE` 判定逻辑未放宽，
  只是让审计与回填读同一份确定性投影；真正被改写的题干仍会被拦。
- 确认结构是**确定性重算重发布**（`runner run --stage structure_gate --force`），不是手改
  `structure.confirmed.json`；批次数据、manifest 完成态均未伪造。

## 如何验证

- `python -m unittest workflow.test_ocr_grid` → **Ran 9 tests OK**。
- 装配相关 9 个套件（test_question_content / test_question_draft / test_assemble_local /
  test_assemble_precheck / test_audit_type_layout / test_wire_format / test_ocr_grid /
  test_empty_image_option_repair / test_phonetic_structure）→ **Ran 130 tests OK (skipped=4)**。
- 真实批次：重发布确认结构后再跑 `runner run --stage assemble` →
  `{"deterministic": true, "model_calls": 0, "report": {"completed": true, …}}`；
  随后 `runner run` 自动进入 meta/underlines/validate，**本地体检通过**（`✓ 本地体检通过`），
  review 两轮审核进入执行。
- 复现脚本：`.content-agent/probe-assemble-replay.py`（修复前 `audit_batch_source` 返回
  `SOURCE_STEM_MISSING u24/4`，修复后 `{"passed": true, "issues": []}`）。

## 回退方式

`git revert` 本提交：`_grid_source_audit` 与 `restore_source_stems` 的网格分支是独立代码块，
删除后行为回到「线性回填 + 网格审计」，即重新报 `SOURCE_STEM_MISSING`（不会静默放宽）。
