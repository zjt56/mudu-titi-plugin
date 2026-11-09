# 2026-11-08 表格图片与文字放回同一单元格

## 背景

上一版把表格行内图片放到行尾独立单元格，用户要求图片必须和它所在单元格的文字放在一起，不要分开。

## 改了什么

- `app/workbench/server.py::source_blocks`：按单元格切分每行，逐单元格收集 `[IMG ...]` 图片名，输出 `cellImages[行][列] = [图片名...]`；单元格文本去掉 IMG 标记但保留原有文字。
- `app/workbench/server.py::attach_media`：为 `table.cellImages` 每张图片补 `{name,url}`。
- `lib/client.js`：表格渲染时每个 `<td>/<th>` 内先显示该单元格图片（极小缩略图），再显示该单元格文字；不再新增行尾图片单元格。

## 验证

- 2022 卷 group-56-57-58：行 1 列 0 同时含 image10.jpeg 与 `<br>Weather…` 文字；行 2/3/4 同理绑定 image11/12/13；URL 全部有效。
- 最小回归：`[IMG image10.jpeg]<br>Weather text` → 同一单元格文本 + 该单元格图片；跨列 `A|B` 的 cellImages 为 `[[],[]]`。
- `node --check lib/client.js`、`py_compile workbench/server.py` 通过；前端测试 8 项通过。

## 边界

- 只改预览渲染，不改题目数据与用户批次。
