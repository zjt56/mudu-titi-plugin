# 2026-11-17 视觉栏目支持 16 页整卷证据

## 背景 / 触发

`app/workflow/visual_columns.py` 曾把整卷页面数大于 12 直接判为 `COLUMN_SCOPE_TOO_LARGE`。这会阻断当前真实的 16 页 PDF 批次，尽管模型请求和证据校验可以完整处理这些页面。

## 改了什么

- 移除按页面数量 `>12` 的阻断；继续保留整卷正文长度上限 `150000` 字符及现有 JSON、来源引用、页坐标和模型响应校验。
- 无损 contact sheet 超过内存保护阈值时，不再报错或省略页面，改为把全部原始整页图片作为多图请求输入，保持每页原始尺寸和 page ID。
- 增加 16 页回归测试，验证一次模型请求收到完整 1..16 页且不产生 `COLUMN_SCOPE_TOO_LARGE`。

## 没改什么 / 边界

- 不截断正文、不拆分或省略页面，不修改用户批次数据。
- 视觉模型仍必须在一次调用中返回覆盖整卷的证据；文本过长、来源变化、响应结构/引用/坐标不合格仍失败。

## 如何验证

- `PYTHONUTF8=1 python -m unittest workflow.test_visual_columns workflow.test_visual_transport workflow.test_source_coordinate_projection workflow.test_denoise`（56/56 通过）。
- 实批 `【木牍教育】2024中考英语试卷` 已用完整 16 页发起整卷复验；当前一次返回 `COLUMN_OWNERSHIP_UNRESOLVED`，随后同一路由重试返回 `PROVIDER_CALL_FAILED`，因此结构闸门仍保持阻断，未宣称批次完成。

## 回退方式

恢复 `visual_columns.py` 中的旧页面数量判断和 contact-sheet 超限异常即可；不会影响已归档批次。
