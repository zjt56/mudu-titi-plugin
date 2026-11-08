# 2026-11-08 预览丢失题组材料：第一篇阅读只剩选项

## 背景 / 触发

用户反馈：2024 卷第一篇阅读理解在界面上**正文内容消失，只剩选项**。这是严重错误，
之前的检查没发现。

排查结论：**批次数据是完整的，问题在预览切片**。

- `06_batch/batch_meta.json` 中 `group-56-57-58` 的 `material` 有 736 字符、
  `materialBlocks` 有 1 个表格块，`[TABLE 2]` 海报材料都在。
- 正文 `01_source/正文.txt` 第 153–164 行确实是 `A` / `[TABLE 2]` / 海报三行 / `[/TABLE]`
  / `※Notes：1.register（登记）　2.contact（联系）`，然后才是第 56 题。
- 但 `preview.slice_range()` 向上补材料时，在 `※Notes：1.…2.…` 这一行命中
  `_weak_numbers()`（把 "2." 当成题号空位），直接 `break`，导致整张表格材料被丢弃，
  预览只剩 56–58 与选项。

## 改了什么（app/workflow/preview.py::slice_range）

1. 向上补材料时，遇到 `[TABLE N]…[/TABLE]` **整块纳入**，不再逐行判断，
   表格前的 `※Notes` 备注与 `A` 分段标签也一并保留，且继续向上找分段标签。
2. 向下收集时，表格内部行不做 `material_label` 截断；表格块整体保留。
3. 最终过滤只在表格外剔除孤立的分段标签 `A`/`B`，表格内容原样保留。

数据未改动，仅预览切片修正。

## 没改什么 / 边界

- 不改任何题目、答案、解析、知识点、装配产物。
- 不改上传字段；`material` / `materialBlocks` 一直是完整的。
- 典型选择题/补全对话/单词拼写/书面表达切片实测不受影响。

## 如何验证

- 新增回归 `workflow.test_source_structure.SourceBoundaryRegressionTest.
  test_reading_table_material_before_first_question_is_not_dropped`：断言切片含
  `[TABLE 2]`、`WATER SAFETY FOR ALL`、`※Notes`、`56.` 且表格在题目之前。
- `workflow.test_source_structure` + `test_writing_preview` + `test_preview_labels`
  共 26 项通过；单独重跑新用例通过。
- 真实 2024 卷：`slice_range([56,57,58])` 由 10 行（无表格）变为 16 行，
  含完整 `[TABLE 2]` 块；`source_blocks` 输出首个块为 table（3 行）。

## 回退

还原 `preview.slice_range` 的向上/向下补材料逻辑即可；不要回退 `AGENTS.md` 规则 14。
