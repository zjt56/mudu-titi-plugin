# 2026-11-08 表格改为自适应宽度并自动断行

## 背景

表格单元格里放了图片和长文字后，表格被撑宽，右侧需要左右滑动。用户要求表格自适应面板宽度、单元格文字自动断行，不要横向滚动。

## 改动

- `lib/client.js`：
  - `.wf-table` 增加 `width:100%;max-width:100%;table-layout:fixed`，表格严格贴合容器宽度，不再被内容撑宽。
  - `.wf-table td/th` 增加 `overflow-wrap:anywhere;word-break:break-word;white-space:normal`，长文字自动折行。
  - `.wf-table-imgcell` 由 `white-space:nowrap` 改为 `white-space:normal`，与文字一致断行。
  - 表格外容器去掉 `overflowX:auto`，改为 `maxWidth:100%;minWidth:0`，不再出现横向滚动条。
  - `.wf-body-image-frame` 改为 `width:100%;max-width:200px`，随容器自适应。

## 验证

- `node --check lib/client.js` 通过；`backend/settings-navigation` + `style-inject` 8 项通过。
- 表格 `table-layout:fixed` + 单元格 `break-word`，长材料与图片在同一单元格内自动断行显示，无需左右滑动。

## 边界

- 仅样式调整，不改题目数据、预览内容或用户批次。
