# 来源坐标终止边界修复

## 背景/触发

用户反馈批次「【木牍教育】2024中考英语试卷」在 `structure_gate` 失败：
`SOURCE_COORDINATE_MISMATCH: source_end_line=308 outside raw body 1..307`。
重新上传后已取得实批目录 `C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】2024中考英语试卷`；已读取 `01_source/正文.txt` 与 `01_source/structure.json` 复验。

## 改了什么

- `app/workflow/denoise.py::project_structure`：当原文以换行结束时，仅允许 `source_end_line`/`end_line` 等于 `len(raw_lines)+1` 的唯一终止边界，并将其投影到最后保留正文行。
- 起始坐标、题目锚点、被删除行上的锚点，以及大于终止边界的坐标仍严格报 `SOURCE_COORDINATE_MISMATCH`。
- `app/workflow/test_source_coordinate_projection.py`：新增 307 行正文 + 尾换行 + 端点 308 回归，并覆盖真实锚点 308 与端点 309 的拒绝反例。
- 实批 `正文.txt` 尾换行下 `source_end_line=308` 已成功投影；`unresolved[0].line=309` 属于 `ANSWER_APPENDIX_DETECTED` 诊断坐标，保留原值而不当作题目锚点。

## 边界

本修复只处理去噪投影的终止端点边界，不放宽题号、题干、选项、材料、来源归属或上传校验；没有该批次原始正文与 `structure.json` 前，不把合成回归视为实批已恢复。未自动修改用户批次、manifest、收据，也未上传或发布。

## 验证

- `PYTHONUTF8=1 python -m unittest workflow.test_source_coordinate_projection -v`：14/14 通过（含答案附录诊断坐标回归）。
- `PYTHONUTF8=1 python -m unittest workflow.test_denoise workflow.test_source_coordinate_projection -v`：30/30 通过；`py_compile` 与 `git diff --check` 通过。
- 实批「【木牍教育】2024中考英语试卷」：正文 307 行且尾换行，结构最大端点 308；直接 `project_structure` 成功，结构闸门不再报告 `SOURCE_COORDINATE_MISMATCH`。

## 回退

如需回退，仅回退 `app/workflow/denoise.py` 的终止边界分支及对应回归，不触碰用户批次数据或台账历史。
