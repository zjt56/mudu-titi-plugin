# 2026-11-11 Word 提取不再对嵌入图跑本地 OCR（改由视觉复核）

## 1. 背景 / 触发

用户反馈「原卷提取很慢」。实测 04 批次（source.docx）：extract 跑了约 4 分钟，根因是
docx 因 Symbol font / Floating object 触发，对 13 张嵌入图**逐张串行跑本地 paddle OCR**
（单次最慢 90.8s/49.9s/34.7s，合计约 236 秒）。用户 2026-11-11 策略：纯 Word 直接文字
提取、不跑 OCR，图片语义交给本地 Harness 视觉理解。并明确「连 warning 提示也不要加」。

## 2. 改了什么

- `app/workflow/extract.py` docx 分支：删除对嵌入图逐张本地 OCR 的循环（ocr_backend.recognize）。
  - 有触发（image_only/floating_object/symbol_font）时只记录内部 `ocr_auto`
    `{mode:"word_visual_only", triggers, skipped:"word_embedded_images_deferred_to_visual_review"}`，
    **不本地 OCR、不阻断、不加 warning**。
  - 图片语义改由 extract 之后的视觉复核（visual_review → image_semantics.json 的 readable_text）提供。

## 3. 没改什么 / 边界

- PDF 源、图片上传源的 OCR 路径不变（仍按文字层优先、不足才 OCR，遵硬规则 28 的 OCR 顺序）。
- 主正文仍来自 docx 原生解析（逐字可信）；本次只是不再用 OCR 冗余补图内文字。
- 视觉复核仍只产出候选证据，不改原文、不据此放行闸门（见 visual-image-review 记录）。

## 4. 如何验证

- 真机对 04 source.docx 重跑 extract_source：**0.08 秒**（改前约 236 秒），OCR 目录 0 个、
  image_ocr 无、ocr_auto=word_visual_only；提取结果不变——characters 1822、questions 15、
  sections 3、passed True，与改前完全一致。
- `workflow.test_extract`：30 例全过；`test_extract + test_source_policy`：52 例全过。


以上验证结果均通过。
## 5. 回退方式

恢复 extract.py docx 分支的逐图 OCR 循环即可（备份见 /tmp/extract.py.bak-*，或 git revert）。
