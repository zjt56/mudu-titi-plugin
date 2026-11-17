# 2026-11-17：画线交付相对目录误判与音频附件导致 underlines 假过期

## 背景/触发

`19年安徽中考英语-2` 组装完成后，`underlines` 显示：

```
← 需重跑（阶段产物内容已变化）
```

手工复验发现两个叠加问题：

1. `handoff.inspect_stage` 用相对 `batch_dir` 时没有先解析为绝对路径，
   `question_draft._safe` 把正常批次文件误判为 `Question draft path escapes batch`；
2. `_assert_underline_projection` 把音频附件（后期媒体字段）也纳入画线视图比较，
   audio 交付后 `batch_meta.json` 新增音频附件，导致 underlines 被误判为源内容变化。

## 改了什么

- `app/workflow/handoff.py::inspect_stage`：`batch_dir` 先 `expanduser().resolve()`，相对路径不再触发路径越界。
- `app/workflow/handoff.py`：抽出 `_without_audio_fields()`，`_assert_meta_projection` 与
  `_assert_underline_projection` 都先剥离音频附件；音频是后置媒体接线，不属于画线/元数据源投影。
- `19年安徽中考英语-2`：调用受控刷新 `_refresh_assembled_underline_delivery`，`underlines`
  恢复 `completed`，不再显示“需重跑”。

## 没改什么 / 边界

- 不把音频附件伪装成画线内容，也不放宽题干/下划线/材料来源校验；
- 不重跑用户已上传批次；17 等旧批次的画线产物仍按历史留档处理。

## 如何验证

- `workflow.test_late_delivery`、`test_late_delivery_receipt`、`test_source_coordinate_projection`、
  `test_content_agent`、`test_content_integration`：78 项通过。
- 19-2 `underlines` 状态 completed；17 旧批次 `inspect_stage('underlines')` 通过。
