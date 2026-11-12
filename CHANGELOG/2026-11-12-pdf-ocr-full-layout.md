# 2026-11-12 上传 PDF 一律整卷 OCR，JSON 作 Word 版面参考

## 背景（触发）

用户（2026-11-12）：「有PDF文件上传时，需要调用OCR识别的功能，产生的JSON文件作为Word的版面参考辅助」。

原行为：Word + PDF 同批时，配套 PDF 只在 OCR 总开关打开或文字层质量不足时才真正 OCR——`app/workflow/pdf_ocr.py::run` 对 Word 主源传入 `force_ocr=False, mandatory_pdf=False`，文字层合格时直接读文字层，`01_source/pdf-ocr.json` 里没有 OCR 版面块。

追问后用户明确选择「有 PDF 就整卷 OCR 出 JSON」：不管总开关是否关闭、文字层是否合格，都要产出 JSON 作 Word 的版面参考。

## 改了什么

- `app/workflow/pdf_ocr.py::run`：删除基于 `manifest.inputs.source` 是否为 docx 的 `structural_reference` 分支，每个去重后的 PDF 一律调用 `ocr_backend.recognize(..., force_ocr=True, mandatory_pdf=True)`；快照目录、`acquisition.json`、`01_source/pdf-ocr.json` 的结构不变。
- `mandatory_pdf=True` 使 `ocr_backend.recognize` 在 OCR 辅助总开关关闭时仍处理上传 PDF（`app/workflow/ocr_backend.py:284` 的豁免路径），保证 JSON 必定尝试产出。
- 文档：`app/docs/extraction.md` 新增「PDF 版面证据（上传即整卷 OCR）」一节，并修正「只有 PDF 不整卷 OCR」与「OCR 总开关」两处旧描述；根 `AGENTS.md` 硬规则 28 增加「上传 PDF 一律整卷 OCR 出 JSON」，落地列表补 `pdf_ocr.py`。
- 测试：`app/workflow/test_pdf_ocr.py` 断言所有 PDF 的调用参数均为 `force_ocr=True, mandatory_pdf=True`，并新增 `test_companion_pdf_of_word_batch_is_fully_ocrd` 覆盖 Word + PDF 同批的配套 PDF。

## 没改什么 / 边界

- Word 仍是内容权威：`app/workflow/extract.py::_pdf` 只在 `needs_ocr` 页用 OCR 文字替换，保留 `native_text/native_blocks`，Word 正文不会被 OCR 覆盖。
- OCR 结果不证明质量或顺序通过：版面块存在不证明阅读顺序正确，顺序问题由识别服务侧排查，不在此处手工重排或硬编码。
- 失败语义不变：识别未配置、超时或空结果时该 PDF 记为 failed，不写伪 JSON；Word 为主源时只记 warning 继续用 Word，PDF 为主源时提取失败。
- 不改历史批次、不改上传边界、不自动上传。
- 已知代价：整卷 OCR 更慢并消耗识别额度（用户已确认接受）。

## 如何验证

- `PYTHONUTF8=1 PYTHONPATH=app python -m unittest workflow.test_pdf_ocr workflow.test_pdf_ocr_integration workflow.test_ocr_backend` → Ran 31 tests, OK (skipped=1)。
- 新增 `test_companion_pdf_of_word_batch_is_fully_ocrd` 验证 Word 主源时配套 PDF 仍以 `force_ocr=True, mandatory_pdf=True` 提交一次。
- `test_ocr_backend::test_mandatory_uploaded_pdf_ocr_bypasses_general_assist_off` 覆盖总开关关闭时 mandatory PDF 仍识别。

## 回退方式

把 `app/workflow/pdf_ocr.py::run` 的 `recognize(...)` 调用恢复为按 `manifest.inputs.source` 是否 docx 计算 `structural_reference` 的旧写法，并同步恢复测试断言与文档即可；无数据迁移。
