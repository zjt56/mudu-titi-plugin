# 2026-11-17：书面表达 section end 超出正文行长导致结构确认失败

## 背景/触发

批次 `【木牍教育】19年安徽中考英语-2` 点击继续处理即报：

```
SOURCE_COORDINATE_MISMATCH: source_end_line=319 outside raw body 1..315
```

该卷 `structure.json` 把 `Ⅺ.书面表达` 的 `source_end_line` 记为 319，但清理后的正文只有
315 行；319 之后是文末答案标题/答案册（`ANSWER_APPENDIX_DETECTED` line 320），不是作文正文。
`denoise.project_structure` 只允许 `len(raw_lines)+1` 的唯一终止边界，无法接受 319（超出4行）。

## 改了什么

- `app/workflow/denoise.py::project_structure`：仅对 `type` 含 `书面表达` 的 section，
  当 `source_end_line > len(raw_lines)` 时夹到最后一行；普通大题、起始坐标、题目锚点和
  被删除行上的锚点仍严格报 `SOURCE_COORDINATE_MISMATCH`，不放宽。
- `app/workflow/test_source_coordinate_projection.py`：新增作文 section end 越界夹取回归。
- 批次 19-2 重跑 `structure_gate`：48 单元（独立36/组合12）通过，可进入 `preassemble`。

## 没改什么 / 边界

- 只处理书面表达合法尾部；不把答案册内容并入作文，不影响答案附录分离。
- 不改变上传题号投影（作文仍为 `null/null`）和其他来源审计规则。

## 如何验证

- `workflow.test_source_coordinate_projection`：14 项通过（新增作文越界回归）。
- 真实批次 `【木牍教育】19年安徽中考英语-2`：`structure_gate` 通过，48 单元。

## 回退方式

删除 `project_structure` 中仅针对 `书面表达` 的尾部夹取分支即可恢复原严格越界报错。
