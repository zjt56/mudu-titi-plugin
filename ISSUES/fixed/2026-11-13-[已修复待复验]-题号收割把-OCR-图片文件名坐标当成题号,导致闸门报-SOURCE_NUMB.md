# 卡点：[已修复待复验] 题号收割把 OCR 图片文件名坐标当成题号，导致闸门报 SOURCE_NUMBERS_MISSING

- id: 2026-11-13-[已修复待复验]-题号收割把-OCR-图片文件名坐标当成题号,导致闸门报-SOURCE_NUMB
- status: fixed
- batch: 七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun
- batch_dir: C:\Users\Administrator\.dsh\wf-plugin-data\workflows\七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun
- stage: structure_gate
- check: SOURCE_NUMBERS_MISSING
- created: 2026-11-13T17:32:08+08:00
- last_seen: 2026-11-13T17:32:08+08:00
- occurrences: 1
- fixed_at: 2026-11-13T19:49:33+08:00
- fix_commit: 8eccde7
- verification: 真实批次 structure_gate 通过且不再出现 SOURCE_NUMBERS_MISSING(50/52/158… 图片名坐标)；test_extract_refresh 9 项 OK；结构/提取相关套件全绿。
- title: [已修复待复验] 题号收割把 OCR 图片文件名坐标当成题号，导致闸门报 SOURCE_NUMBERS_MISSING

## 现象（卡点）

structure.json 第20行大题 questions=[1,2,3,4,5,50,52,55,158,184,192,325,328,380,392,446,500,617,623,727,734,782,791,793,888,891,941]；第104行=[1,2,3,557,639,640,680,720,721,854,855,969,973,989]。这些数字全部来自 OCR Markdown 里的 imgs/img_in_image_box_52_617_158_734.jpg 文件名坐标。

## 具体检查与判定

- 失败检查：SOURCE_NUMBERS_MISSING
- 判定口径：正文是 OCR Markdown，图片以 <div><img src=imgs/img_in_image_box_X_Y_W_H.jpg> 或 [IMG imgs/img_in_image_box_X_Y_W_H.jpg] 形式出现；extract.blank_numbers/same_line_numbers、preview._weak_numbers、structure_gate._anchor_text 只去 HTML 标签、未去图片文件名，W/H 坐标被当成印刷小题号，闸门据此判定原卷缺少这些题号并阻断。
- 修复方向：source_anchors.strip_visual_markup 统一剥离 img_in_image_box 图名与 HTML 标签（保留 [IMG …] 占位以免影响图注空），题号收割/弱题号/锚点全部复用；新增 runner refresh-extract 按留存 原始正文.txt+extraction.json 确定性重算 structure.json（不重跑 OCR/模型，校验原件 SHA-256、正文一致、下游未完成）；回归 test_extract_refresh.py。

## 修复记录

- 修复方式：source_anchors.strip_visual_markup 统一剥离 HTML/OCR 图片名标记；extract.blank_numbers/same_line_numbers、preview._weak_numbers、structure_gate._anchor_text 全部先剥离再取题号；新增 test_extract_refresh 覆盖 HTML/占位两种图片形态与表格内图片坐标不外泄。
- 修复提交：8eccde7
- 复验证据：真实批次 structure_gate 通过且不再出现 SOURCE_NUMBERS_MISSING(50/52/158… 图片名坐标)；test_extract_refresh 9 项 OK；结构/提取相关套件全绿。
- 标记时间：2026-11-13T19:49:33+08:00
