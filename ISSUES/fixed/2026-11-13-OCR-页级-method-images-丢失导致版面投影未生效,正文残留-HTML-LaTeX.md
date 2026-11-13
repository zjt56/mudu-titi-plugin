# 卡点：OCR 页级 method/images 丢失导致版面投影未生效，正文残留 HTML/LaTeX/###

- id: 2026-11-13-OCR-页级-method-images-丢失导致版面投影未生效,正文残留-HTML-LaTeX
- status: fixed
- batch: 03八上人教版课时A计划Unit1HappyHoliday第三课时Grammar
- batch_dir: workflows/03八上人教版课时A计划Unit1HappyHoliday第三课时Grammar
- stage: extract
- check: OCR_MARKUP_PROJECTION_SKIPPED
- created: 2026-11-13T20:27:13+08:00
- last_seen: 2026-11-13T20:27:13+08:00
- occurrences: 1
- fixed_at: 2026-11-13T20:27:25+08:00
- fix_commit: 08b2cc9
- verification: 隔离副本 refresh-from-evidence：###/</div>/<img>/\$ 全部归零，[IMG] 0→1；test_ocr_markup+test_pdf_ocr+test_ocr_backend+test_extract_refresh 54 passed；合并区大回归 211 passed
- title: OCR 页级 method/images 丢失导致版面投影未生效，正文残留 HTML/LaTeX/###

## 现象（卡点）

正文.txt 含 ###×5、<div>×1、<img>×1、$…$×5，[IMG …]×0；题目带版面代码不干净

## 具体检查与判定

- 失败检查：OCR_MARKUP_PROJECTION_SKIPPED
- 判定口径：pdf_ocr.run 合并页记录只保留 page/text/blocks/warnings，丢掉服务返回的 method 与 images；extract 的投影条件是 method=="ocr"，method=None 时整个 ocr_markup.normalize_document 被跳过，图片也无法落盘成 [IMG]
- 修复方向：pdf_ocr 合并页保留 method/images 并新增 page_evidence() 从 acquisition.json 回补旧批次；extract 投影条件改为 ocr/blocks/images 并在投影前回补；强制 OCR 拒绝原生文字层与整卷空文字；补 test_pdf_ocr/test_extract_refresh 回归

## 修复记录

- 修复方式：pdf_ocr 保留 method/images 并提供 page_evidence 回补；extract 投影条件按 ocr/blocks/images 判定；强制 OCR 拒绝原生层/空文字
- 修复提交：08b2cc9
- 复验证据：隔离副本 refresh-from-evidence：###/</div>/<img>/\$ 全部归零，[IMG] 0→1；test_ocr_markup+test_pdf_ocr+test_ocr_backend+test_extract_refresh 54 passed；合并区大回归 211 passed
- 标记时间：2026-11-13T20:27:25+08:00
