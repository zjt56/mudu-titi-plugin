# 2026-11-08 表格图片按行归位并缩为极小缩略图

## 背景

上一篇把表格内 `[IMG ...]` 抽成独立图片块排在表格后面，导致 2022 年第一篇阅读（group-56-57-58）图片位置错乱。用户要求：表格每一行对应自己的图片，位置必须在行内，且缩得非常非常小。

## 改了什么

- `app/workbench/server.py::source_blocks`：表格行不再把图片拿出表格，而是把每行图片名记录到与 `rows` 对齐的 `rowImages` 数组；单元格文本去掉 IMG 标记。
- `app/workbench/server.py::attach_media`：为 `table.rowImages` 中每个图片补 `{name,url}`，与顶层图片一致做存在性校验。
- `lib/client.js`：表格渲染按行索引把 `rowImages[ri]` 作为行尾极小缩略图（44×34px）显示；正文图片框进一步 `320px → 200px`。
- 新增最小回归：表格行内图片保留在 `rowImages` 对应行，顶层不再突兀出现独立 image 块。

## 验证

- 2022 卷 group-56-57-58：解析出 5 行表格，行 1~4 分别绑定 image10~13，URL 均有效。
- `node --check lib/client.js`、`python -m py_compile workbench/server.py` 通过。
- `backend/settings-navigation` + `style-inject` 8 项通过；表格行图片最小回归通过。

## 边界

- 只改预览渲染与尺寸，不改题目数据和用户批次。
