# 2026-11-17：提取诊断行超出清理正文导致 SOURCE_COORDINATE_MISMATCH

## 背景/触发

批次 `【木牍教育】16年安徽中考英语` 点击继续处理后失败：

```
SOURCE_COORDINATE_MISMATCH: line=332 outside raw body 1..278
```

`structure.json` 的 sections/groups 坐标都在 278 行以内；超界的 332 来自
`unresolved` 诊断：

```json
{"code": "QUESTION_NUMBER_INFERRED", "line": 332, "message": "Ⅺ.书面表达...候选编号 [96]"}
```

提取器用原始全文（含答案附录）记录诊断行，而 `正文.txt` 已清理为 278 行；
`denoise.project_structure` 把所有 `line` 都当题目锚点投影，于是作文题号推断诊断
把整个结构闸门卡死。

## 改了什么

- `app/workflow/denoise.py::project_structure`：`unresolved` 作为提取诊断原样保留，
  不参与坐标投影；sections/groups/question_locations 等真正的题目锚点仍严格校验，
  越界/删除行锚点仍报错。
- `app/workflow/test_source_coordinate_projection.py`：新增 unresolved 越界诊断回归。
- 批次 16 重跑 `structure_gate`：48 单元（独立36/组合12）通过。

## 没改什么 / 边界

- 不放宽题目、题干、选项、材料锚点的越界校验；
- 不删除 `unresolved` 诊断，仍保留原始行号供排查；
- 不改变 writing section end 的既有夹取逻辑。

## 如何验证

- `workflow.test_source_coordinate_projection`：14 项通过。
- 批次 `【木牍教育】16年安徽中考英语`：`structure_gate` completed，48 单元。

## 回退方式

删除 `project_structure` 中 `unresolved` 的保留/跳过投影分支即可恢复旧行为。
