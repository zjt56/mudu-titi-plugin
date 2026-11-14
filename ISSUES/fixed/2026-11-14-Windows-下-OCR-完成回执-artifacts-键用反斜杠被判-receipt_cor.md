# 卡点：Windows 下 OCR 完成回执 artifacts 键用反斜杠被判 receipt_corrupt

- id: 2026-11-14-Windows-下-OCR-完成回执-artifacts-键用反斜杠被判-receipt_cor
- status: fixed
- batch: 本地回归-无具体批次
- stage: extract
- check: ocr_job_store._validate artifacts 名称规则
- created: 2026-11-14T09:35:47+08:00
- last_seen: 2026-11-14T09:35:47+08:00
- occurrences: 1
- fixed_at: 2026-11-14T09:36:55+08:00
- fix_commit: 5cb36fe
- verification: test_ocr_job_store+test_paddle_jobs 22 项通过；OCR/提取/消费 14 模块 235 项 234 通过 1 跳过 0 失败(102.94s, exit 0)；修复前 4 项 ERROR receipt_corrupt
- title: Windows 下 OCR 完成回执 artifacts 键用反斜杠被判 receipt_corrupt

## 现象（卡点）

拉取 c432de9 后跑 OCR 回归 4 项 ERROR：StoreError: receipt_corrupt（test_ocr_job_store.test_completion_manifest_checks_media_bytes_and_core_artifacts）；paddle_jobs/ocr_backend 报 'OCR task receipt is invalid; automatic resubmission refused.'（test_paddle_jobs 三项）

## 具体检查与判定

- 失败检查：ocr_job_store._validate artifacts 名称规则
- 判定口径：JobStore.complete() 用 str(path.relative_to(directory)) 生成回执键，Windows 得到 media\\page-0001-images-001.png，不匹配 _validate 的 media/page-... 规则，合法完成回执被判损坏；PDF 强制 OCR 在 Windows 将整体失败
- 修复方向：改为 path.relative_to(directory).as_posix() 生成 POSIX 键，并在 test_ocr_job_store 补断言锁住键格式

## 修复记录

- 修复方式：JobStore.complete() 用 relative_to(...).as_posix() 生成 POSIX 回执键；test_ocr_job_store 补断言锁住 media/page-... 形式，反斜杠键不得出现
- 修复提交：5cb36fe
- 复验证据：test_ocr_job_store+test_paddle_jobs 22 项通过；OCR/提取/消费 14 模块 235 项 234 通过 1 跳过 0 失败(102.94s, exit 0)；修复前 4 项 ERROR receipt_corrupt
- 标记时间：2026-11-14T09:36:55+08:00
