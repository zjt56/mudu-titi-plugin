# OCR 正文残留 `<div>/###/LaTeX`：投影从未生效（页级 method/images 丢失）

## 背景/触发

用户反馈：OCR 识别出来的题目“不干净”，正文里存在小符号，并问“当前用的是 Markdown 还是
JSON，哪个干净就用哪个”。

排查结论（真实批次 `03八上人教版课时A计划Unit1HappyHoliday第三课时Grammar`）：

- 正文消费的是 **版面 Markdown**（`pdf-ocr.md` 的页 `text`），JSON（`pdf-ocr.json`）
  只用于版面块/坐标与来源证明；两者文字同源。
- 该批次 `正文.txt` 实测残留：`###` ×5、`<div>` ×1、`<img>` ×1、`$…$` ×5，
  `[IMG …]` ×0 —— 说明 `ocr_markup.normalize_document` 的投影**整个没执行**。
- 根因：`pdf_ocr.run()` 合并页记录时只保留 `page/text/blocks/warnings`，丢掉服务已经
  返回的 `method` 与 `images`（它们真实存在于
  `01_source/pdf-ocr/<源>/acquisition.json`）。`extract._publish_analysis` 的投影条件是
  `any(p.get("method") == "ocr")`，`method=None` 时直接跳过，于是 HTML/LaTeX/`###`
  原样进了正文；`images` 缺失又让图片无法落盘成 `[IMG …]`。
- `pdf-ocr.json` 与 Markdown 谁更干净：**文字同源，噪声相同**；单纯改用 JSON 块并不能
  去掉 `$ \underline{…} $`。Markdown 还额外保留表格 HTML 与图片引用，投影后可完整变成
  `[TABLE n]`/`[IMG …]`。所以正确做法不是换成 JSON，而是**让 Markdown 的投影真正生效**。

## 改了什么

- `app/workflow/pdf_ocr.py`
  - 合并页记录时保留 `method`（服务未回时按是否有 blocks/images 判定为 `ocr`）与 `images` 映射；
  - 新增 `page_evidence(batch, sha)`：从留存的 `acquisition.json` 回补旧批次页级
    `method/images`，只读服务自身证据，不重新识别、不改文字。
- `app/workflow/extract.py`
  - 投影条件改为 `method == "ocr" or blocks or images`，并在投影前用
    `page_evidence` 回补缺失的页级证据；
  - OCR 视觉复核标记同样按 `ocr/blocks` 判定。
- `app/workflow/ocr_backend.py`：错误允许表补 `native_text` / `empty_document`，
  配合强制 OCR 拒绝“原生文字层冒充识别”和“整卷空文字”。
- `app/workflow/pdf_ocr.py`：强制 OCR 增加 `method == text` 与整卷空文字两条拦截
  （与远端 mandatory-OCR 口径一致）。

## 没改什么 / 边界

- 不重排识别顺序、不改写文字；原始 Markdown 仍完整留证在 `pdf-ocr.md`/`原始正文.txt`；
- 不换用 JSON 块作为正文源（文字同源且丢表格/图片引用）；
- 图片仍要求能在 `01_source/media` 落盘才转 `[IMG …]`，落不了盘就保留原标记并报
  `OCR_IMAGE_UNRESOLVED`，不猜文件名。

## 如何验证

隔离副本（不改用户批次）复跑 `--refresh-from-evidence`：

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| `###` | 5 | 0 |
| `<div>` | 1 | 0 |
| `<img>` | 1 | 0 |
| `$` | 5 | 0 |
| `[IMG …]` | 0 | 1 |

直接对 `acquisition.json` 的页图映射跑投影：`[IMG page-0001-images-001.jpg]`，
未解析 `<img>` 为 0。

回归：`workflow.test_pdf_ocr`、`workflow.test_ocr_backend`、`workflow.test_ocr_markup`、
`workflow.test_extract` 全部通过（合并后共 65 项在该批次相关套件）。

## 回退方式

删除 `page_evidence` 调用与 `method/images` 保留逻辑即可回到“投影不生效”的旧行为
（不建议，正文会重新带版面代码）。
