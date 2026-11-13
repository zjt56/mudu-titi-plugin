# 2026-11-13 PDF OCR 版面 Markdown 投影：正文/题干/审核预览不再显示 HTML 与 LaTeX

## 背景/触发

用户反馈「最新的任务识别出来在试卷审核页面都是各种代码」。批次
`七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun` 是 PDF 单页（Pronunciation），
按硬规则 28 整卷 OCR 后，识别服务（PaddleOCR-VL）返回的**版面 Markdown** 被原样当成正文：

- `01_source/正文.txt` 里有 `<div style="text-align: center;"><img src="imgs/img_in_image_box_52_617_158_734.jpg" .../></div>`（7 处）；
- 画线是 LaTeX：`$ \underline{Wake} $ up,little $ \underline{Kate} $!`（34 处 `\underline`）；
- `06_batch/question-draft.json` 的 `material`/`stem` 逐字继承这些标记；
- 审核页原卷预览的 `source_blocks` 只认 `[IMG 媒体名]` 标记，于是把 HTML 标签当文字显示成“代码”。

## 改了什么

1. 新增 `app/workflow/ocr_markup.py`：确定性投影（不改字、不重排、不猜合并）。
   - `<img src>` → `[IMG 媒体名]`（沿用原生 PDF 的 `pdf-page-{页}-image-{序}{后缀}` 命名）；
   - HTML `<table>` → `[TABLE n]` + 每行 `cell | cell`（`colspan`/`rowspan` 只以空单元格占位，不安猜合并）；
   - `\underline{…}` → `<u>…</u>`，`\text{…}` 等包装 → 纯文字；真数学式（仍含 `\ { } ^ _`）原样保留；
   - Markdown 标题 `#`、粗体 `**` 去标记；
   - `unresolved_images()` 报告未命中落盘的图片，`normalize_document()` 按 `[PAGE n]` 用各页的图片映射。
2. `app/workflow/extract.py`：
   - `_pdf()` 把 OCR 结果的 `image_dir` 带进 `result`；
   - 新增 `_ocr_media_roots()` / `_resolve_ocr_image()`：依次按 `result["artifacts"]["attempt_dir"]`
     （识别服务真实的 `paddle-ocr-*/media` 目录）、`result["image_dir"]`、`pdf-ocr.json` 里的 `result_dir`
     定位页图（相对路径按批次根解析；`result_dir` 本身没有 `media/` 时用 `rglob` 唯一命中兜底，多个命中不采信）；
   - 新增 `_publish_ocr_media()`：把页图复制到 `01_source/media/`（同名不同摘要先写 `.bak`，可重复执行）；
   - `_publish_analysis()`：`原始正文.txt` 仍写原始 OCR Markdown（证据），`正文.txt`/结构/元数据用投影文本；
     投影生效时 `extraction.json.text_projection="ocr-markup-v1"`；未落盘图片保留原标记并报
     `OCR_IMAGE_UNRESOLVED` / `OCR_MEDIA_WARNING`（强制 `layout_required`），不静默丢图。
3. 回归：
   - 新增 `app/workflow/test_ocr_markup.py`（17 项）：图片/表格/画线/数学/标题投影、`[PAGE n]` 分页映射、
     端到端 `_publish_analysis`、图片缺失保留标记、`image_dir` 缺失时按 `pdf-ocr.json` 回查、
     识别服务把页图放在 `paddle-ocr-*/media` 嵌套目录时仍能落盘；
   - `test_extract_refresh.py` 的旧注释改为「未投影的原始标记不得变成题号（生产路径由 ocr_markup 先换 [IMG]）」。

## 没改什么 / 边界

- 原始 OCR Markdown 不删除、不改写：仍留在 `01_source/pdf-ocr.md`、`原始正文.txt` 识别服务的 acquisition 里。
- 投影是纯确定性规则，不调用模型、不补字、不重排顺序；识别服务返回的错误顺序仍如实留证（`OCR_VISUAL_REVIEW`）。
- 只处理 `method=="ocr"` 的页；Word/text-layer 提取路径不变。
- 不改题号、题型、审核口径与上传边界。

## 如何验证

- `cd app && PYTHONPATH=. python -m unittest workflow.test_ocr_markup workflow.test_extract workflow.test_extract_refresh`
  → **56 项 OK**。
- 真实批次（18:25 重新提取）：
  - `01_source/正文.txt` 2120 字符，`<img` 0、`<div` 0、`[IMG` 11、`<u>` 34；
  - `01_source/media/` 落盘 `pdf-page-1-image-1..11.jpg`；`extraction.json.text_projection="ocr-markup-v1"`；
  - `structure.json.unresolved` 不再含 `OCR_IMAGE_UNRESOLVED` / `OCR_MEDIA_WARNING`；
  - 展示层：`preview.source_slice` 切出的窗口含 6 个 `[IMG …]`、`<u>` 画线，0 个 `<img>`；
    `workbench.server.source_blocks` + `attach_media` 生成 6 个 `image` 块，URL 指向
    `/api/batches/<批次>/media/pdf-page-1-image-N.jpg`。

## 回退方式

删除 `app/workflow/ocr_markup.py`、还原 `app/workflow/extract.py` 的 `_publish_analysis` /
`_ocr_media_roots` / `_publish_ocr_media` 改动，并对受影响批次重新提取即可回到“正文原样保留 OCR Markdown”的旧行为。
