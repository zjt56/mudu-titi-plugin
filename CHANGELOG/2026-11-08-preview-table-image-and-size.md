# 2026-11-08 修复表格内图片预览缺失并缩小原图显示

## 背景

2022 年安徽卷第一篇阅读理解（group-56-57-58）的图片在原卷预览里不显示。核对发现该篇材料是表格型，图片 `[IMG image10.jpeg]` 等写在 `[TABLE ...]` 单元格内（`[IMG image10.jpeg]<br>Weather`），而后端 `source_blocks` 只按 `|` 切单元格，把图片当成单元格文本，导致图片块丢失。用户同时要求原卷图片显示小一点，不要占太大。

## 改了什么

- `app/workbench/server.py::source_blocks`：解析表格时先将单元格内 `[IMG ...]` 抽成独立 image 块（表格渲染后按原顺序追加），单元格文本保留（去掉 IMG 标记）。不再丢表格内图片。
- `lib/client.js`：
  - 正文图片框 `.wf-body-image-frame` 宽度 `560px → 320px`。
  - 选项图片框 `.wf-opt-frame` 高度 `90px → 72px`。
  - 图片仍 `object-fit:contain`，不裁切、不变形。

## 边界

- 只改预览渲染，不改题目数据、不动用户批次。
- 图片缺失/无法核验仍属能力受限，不进人工、不阻断（沿用既有规则）。

## 如何验证

- 2022 卷 group-56-57-58：`source_blocks` 现在解析出 4 个 image 块（image10~13）+ 1 个 table 块，媒体文件均存在。
- `node --check lib/client.js` 通过；`backend/settings-navigation` + `style-inject` 8 项通过。
- 表格内图片抽取最小回归：`[IMG image10.jpeg]<br>Weather` → image 块 + 文本保留。
