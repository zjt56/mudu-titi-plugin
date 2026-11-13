# 卡点：PDF OCR 版面 Markdown 未投影：正文/题干/审核预览显示 HTML 与 LaTeX 代码

- id: 2026-11-13-PDF-OCR-版面-Markdown-未投影-正文-题干-审核预览显示-HTML-与-LaTe
- status: fixed
- batch: 七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun
- stage: extract
- check: OCR_MARKUP_PROJECTION
- created: 2026-11-13T18:22:58+08:00
- last_seen: 2026-11-13T18:22:58+08:00
- occurrences: 1
- fixed_at: 2026-11-13T19:03:16+08:00
- fix_commit: e9e2c41
- verification: 真实批次 七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun：01_source/正文.txt 2120 字符、<img 0、<div 0、[IMG 11、<u> 34，media/ 落盘 11 个 pdf-page-1-image-*.jpg；preview.source_slice(unit sec-f84d2b7afb671c5b-u24) 17 行含 6 个 [IMG]、workbench.source_blocks 输出 6 个 image 块且 attach_media 生成 /api/batches/.../media URL；test_ocr_markup 17 项 + 定向 177 项 OK。
- title: PDF OCR 版面 Markdown 未投影：正文/题干/审核预览显示 HTML 与 LaTeX 代码

## 现象（卡点）

01_source/正文.txt 与 06_batch/question-draft.json 的 material/stem 逐字保留识别服务 Markdown：<div style="text-align: center;"><img src="imgs/img_in_image_box_52_617_158_734.jpg" .../></div> 共 7 处、$ \underline{Wake} $ 共 34 处；审核页原卷预览 source_blocks 只认 [IMG name] 标记，于是把标签当文字显示成“各种代码”。

## 具体检查与判定

- 失败检查：OCR_MARKUP_PROJECTION
- 判定口径：硬规则 28 要求 PDF 正文直接消费 OCR 结果，但项目正文标记规范是 [IMG 媒体名]/<u>…</u>/[TABLE n]；OCR 页图从未落盘到 01_source/media（extract._pdf 的 OCR 分支只拼 page.text，extract_source 又对 method=='ocr' 的页 continue 跳过媒体发布），投影缺失导致数据层与展示层同时出现代码。
- 修复方向：新增 app/workflow/ocr_markup.py 做确定性投影（不改字、不重排、不猜合并）：<img src>→[IMG 已落盘媒体名]、HTML 表格→[TABLE n]、LaTeX \underline/\text→<u>…</u>/纯文字、标题与粗体去标记；在 extract._publish_analysis 把 OCR 页图复制到 01_source/media 并投影 正文.txt，原始 Markdown 仍留 原始正文.txt 与 pdf-ocr.md 作证据；补 test_ocr_markup.py（含真实批次失败的 image_dir 缺失/report 回查路径回归）与端到端预览断言。

## 修复记录

- 修复方式：新增 workflow/ocr_markup.py 确定性投影：<img>→[IMG 落盘媒体名]、HTML 表格→[TABLE n]、LaTeX \\underline/\\text→<u>/纯文字、Markdown #/** 去标记；extract.py 发布 OCR 页图到 01_source/media 并写 原始正文.txt(原文证据)/正文.txt(投影)，artifacts.attempt_dir 嵌套媒体按唯一 basename 定位。
- 修复提交：e9e2c41
- 复验证据：真实批次 七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun：01_source/正文.txt 2120 字符、<img 0、<div 0、[IMG 11、<u> 34，media/ 落盘 11 个 pdf-page-1-image-*.jpg；preview.source_slice(unit sec-f84d2b7afb671c5b-u24) 17 行含 6 个 [IMG]、workbench.source_blocks 输出 6 个 image 块且 attach_media 生成 /api/batches/.../media URL；test_ocr_markup 17 项 + 定向 177 项 OK。
- 标记时间：2026-11-13T19:03:16+08:00
