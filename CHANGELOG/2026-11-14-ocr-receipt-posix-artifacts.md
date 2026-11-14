# OCR 完成回执在 Windows 用反斜杠键导致 receipt_corrupt

日期：2026-11-14

## 背景

拉取远端 `c432de9 feat(ocr): 异步任务持久回执、等待续查与结果复用` 后，PDF 强制 OCR 的回归组出现 4 项 ERROR，全部指向新的持久回执：

- `workflow.test_ocr_job_store.test_completion_manifest_checks_media_bytes_and_core_artifacts` → `StoreError: receipt_corrupt`
- `workflow.test_paddle_jobs` 三项 → `OCR task receipt is invalid; automatic resubmission refused.`
- `workflow.test_ocr_backend` 里经 `paddle_jobs.recognize` 的同一路径同样报错。

根因：`JobStore.complete()` 用 `str(path.relative_to(self.directory))` 生成回执 `artifacts` 的键。POSIX 下是 `media/page-0001-images-001.png`，Windows 下是 `media\page-0001-images-001.png`，而 `_validate()` 的媒体名规则只接受 `media/page-...`。于是**识别本身成功、结果文件也已落盘**，但写完回执立即被判为损坏并抛出，`paddle_jobs.recognize` 再转成“回执无效、禁止自动重发”。在 Windows 上这会让每份 PDF 的整卷 OCR 直接失败；由于 2026-11-13 起 OCR 是提取必过条件，等于 PDF 批次全部卡在 extract。

## 改了什么

- `app/workflow/ocr_job_store.py::JobStore.complete`：artifacts 键改为 `path.relative_to(self.directory).as_posix()`，回执键固定为 POSIX 形式（与 `pdf_ocr.py` 既有的 `as_posix()` 口径一致），Windows 读回时 `self.directory / "media/page-..."` 仍可正常解析。
- `app/workflow/test_ocr_job_store.py`：完成清单用例补断言，锁住 `artifacts` 中必须是 `media/page-0001-images-001.png`，且不得出现反斜杠键，防止再次回退。
- `ISSUES/`：本次卡点按规则 24 登记（批次记为「本地回归-无具体批次」，阶段 extract，检查 `ocr_job_store._validate artifacts`）。

## 边界

只改回执键的序列化形式与一条日志级测试断言；不改回执 schema、字段集合、指纹算法、锁语义、提交/查询/下载流程，也不放宽 `receipt_corrupt` 的失败即关闭策略——真正的损坏回执仍然只会失败、不会自动重发。

## 验证

- 修复前：`test_ocr_job_store` + `test_paddle_jobs` 复现 4 项 ERROR（见上）。
- 修复后：
  - `workflow.test_ocr_job_store workflow.test_paddle_jobs`：22 项全部通过。
  - OCR/提取/消费链路合组（`test_pdf_ocr`、`test_pdf_ocr_integration`、`test_ocr_backend`、`test_ocr_job_store`、`test_ocr_pipeline_recovery`、`test_ocr_wait_scheduling`、`test_ocr_acquisition_recovery`、`test_paddle_jobs`、`test_paddle_jobs_recovery`、`test_extract`、`test_ocr_assembly_images`、`test_ocr_assembly_source`、`test_ocr_structure_gate`、`test_workbench_decisions`）：**235 项，234 通过，1 跳过，0 失败**（102.94 秒，exit 0）。

## 回退方式

`git revert` 本提交即可回到「Windows 键用反斜杠」的行为；回退会重新让 Windows 上所有 PDF 的 OCR 回执被判损坏，不建议。
