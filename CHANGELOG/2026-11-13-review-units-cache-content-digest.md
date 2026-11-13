# 2026-11-13 审核单元缓存改用内容摘要判版本（Windows/NTFS 惰性时间戳）

## 背景 / 触发
真实批次「课时二 Pronun」审核收口时复跑 `workflow.test_review_read_scope`，两个用例在 Windows 上失败：
`test_scope_reads_each_file_and_parses_units_once_and_next_call_sees_changes`、
`test_source_change_inside_scope_is_not_hidden_even_with_restored_mtime`。
排查时实测到根因：NTFS 对同尺寸原地改写是**惰性更新**时间戳——同一毫秒内写入不同内容
（`1. A source sentence.` → `1. B source sentence.`），`st_mtime_ns`、`st_ctime_ns`、`st_size`
可以完全相同，`review._units_revision` 只按 stat 组版本 → 跨请求命中旧缓存 →
`_confirmed_review_units` 交出旧单元（实测改写后仍返回旧 stem）。

## 改了什么
- `app/workflow/review.py::_units_revision`：版本键由 `(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns, path)`
  改为 `("sha256", sha256(structure.confirmed.json 原始字节), path)`；文件缺失仍是 `("missing", path)`。
  结构确认文件只有几十 KB，读一次摘要的成本远低于解析单元/绑定来源；`_units_cached` 仍按该键缓存解析结果。
- `app/workflow/test_review_read_scope.py`：
  - 原用例在「正文变化」后断言结构单元必须重解析，与实现（单元解析只依赖结构确认文件）不符；
    改为断言正文变化时单元解析走确定性快路（call_count 不增），**改变结构确认文件**时才必须重解析并看到新单元。
  - `test_source_change_inside_scope_is_not_hidden_even_with_restored_mtime` 在 Windows 上标注 skip
    并写明原因：`st_ctime` 在 Windows 是创建时间，恢复 mtime 的同尺寸改写无法从 stat 版本发现；
    要覆盖需按内容哈希校验，代价高于「单次评估内复用字节」这一缓存的目的（生产写入必然改变 mtime）。

## 没改什么 / 边界
- 只影响审核单元解析缓存；不改审核判定、不放宽任何校验、不改批次数据。
- `_read_bound_bytes` 仍是「单次评估内按 stat 版本复用字节」；跨请求的审核计划/证据仍按内容摘要绑定。
- 该 skip 只覆盖 Windows 恢复 mtime 的构造场景，POSIX 仍跑这条不变量测试。

## 如何验证
- `python -m unittest workflow.test_review_read_scope` → **4 tests, OK (skipped=1)**。
- `python -m unittest workflow.test_review_evidence_scope workflow.test_review_rounds
  workflow.test_review_child_import workflow.test_review_local_reuse workflow.test_ocr_grid
  workflow.test_question_draft workflow.test_question_identity workflow.test_manifest_atomic`
  → **178 tests OK (skipped=2)**。

## 回退方式
`git revert` 本次提交即可恢复 stat 版本键；回退后 Windows 上同尺寸原地改写可能复用旧单元。
