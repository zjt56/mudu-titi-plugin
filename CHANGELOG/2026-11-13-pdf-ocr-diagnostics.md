# PDF OCR 失败诊断透传与指定批次恢复

## 背景

用户报告批次 `03八上人教版课时A计划Unit1HappyHoliday第三课时Grammar` 的 extract 失败。批次目录为 `workflows/03八上人教版课时A计划Unit1HappyHoliday第三课时Grammar`。原失败发生于 2026-11-13 19:42:31—19:42:35，原件 PDF 共 1,328,879 字节，SHA-256 为 `c0822f42781b8f4fffaa67acf8f8df623566edd2c0ee0f65b13268aa5c6f9f2f`。

旧 `pdf-ocr.json` 只保留 `OCRBackendError` 和通用消息，无法区分服务拒绝、网络、任务失败、无效结果；同一 PDF 的 source/docx 别名及异常包装导致重复显示。原调用已进入 Paddle 尝试目录，但没有保存更具体的失败信息，因此本次无法证明最初远端失败的具体根因。

## 修改

- `app/workflow/ocr_backend.py`：受控 Paddle 错误传递固定错误码及脱敏的 HTTP 状态、服务错误码和错误摘要；公开投影不直接使用任意异常字符串。
- `app/workflow/pdf_ocr.py`：保存受控结构化错误；未知异常保持通用信息，不输出私有配置或凭据。
- `app/workflow/extract.py`：汇总按 PDF 摘要去重，不再把同一 OCR 失败重复嵌套；保留明确的安全错误原因。
- 对应测试覆盖诊断透传、凭据/URL 脱敏、未知异常和同源别名去重。

## 实际恢复与验证

- 用原件和现有私有 Paddle 配置独立诊断，服务返回 2 页。未改变服务地址、模型或凭据。
- 对指定活动批次先保留完整临时备份，再通过正式 `workflow.runner run --stage extract --force` 复验，退出码 0。
- 正式产物：2 页 OCR，页文字长度分别为 2115、2068；提取正文 4204 字符、6 个分区、32 个候选题号、7 项提取待核验线索。JSON、Markdown、正文、结构候选、元数据及提取报告均存在，原件摘要一致。
- read-back：extract=completed；后台已自动推进至 structure_gate=completed、preassemble=running；原 extract 运维 incident=resolved。诊断不再列出 extract 阻断。候选题号和阶段完成不构成整卷内容质量已通过。
- 根代理实际运行 `workflow.test_pdf_ocr workflow.test_ocr_backend workflow.test_extract workflow.test_paddle_jobs`：共 77 项，76 项通过、1 项按环境跳过，17.225 秒，退出码 0。`git diff --check` 通过。

## 边界

未替换 OCR 模型、未使用原生 PDF 文字冒充 OCR、未改 OCR 服务阅读顺序、未修改原件或直接编辑 manifest 完成状态、未运行其他批次、未自动上传。原失败的具体服务根因未获证实，本次证明的是该批次已恢复且受控诊断缺陷已修复。

## 回退

可逐项回退本次三个源码及对应测试的补丁。活动批次复验前完整备份位于本机临时目录 `md-ocr-before-jlb2_3s7/03八上人教版课时A计划Unit1HappyHoliday第三课时Grammar`，不得在后台运行中覆盖恢复。

## 版本控制

改动前执行 git fetch；首次网络失败，随后复查本地 master 领先 origin/master 2 个提交、落后 5 个提交。未合并、未提交或推送，避免把并行提交混入本次修复。
