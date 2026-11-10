# 2026-11-10 审核附件上传：Word/PDF 随暂存追加

## 背景

上一轮曾判断题库服务器不支持 Word/PDF 附件，这是错误结论。用户随后提供并指向官方开发者文档 `http://101.43.1.57/docs/#format`：审核附件通过上传暂存的 `/attachments` 接口追加，支持 PDF、DOCX、PNG、JPEG，每份 20,000,000 字节、每批 20 个；创建暂存必须人工会话，至少一个附件才能远端确认；追加非幂等，响应丢失要先核对暂存列表；审核结算后可用 `DELETE /api/reviews/{batch_id}/evidence` 清理审核附件。

本次核对文档与代码后，补齐了这条链路。题目 JSON 里的 `attachments` 仍只允许 image/audio，两类附件不能混用。

## 改了什么

- `upload_client.py`
  - 新增 `staging_attachments()`：读取暂存列表，按 `attachments`（兼容 `files`）返回附件名称；无名称证据时明确报 `staging_attachments_unavailable`，不猜数量。
  - 新增 `append_attachment()`：二进制 `POST /api/uploads/{staging_id}/attachments`，`X-Filename` 为 URL 编码文件名，按扩展名设置 PDF/DOCX/PNG/JPEG 的 Content-Type；单文件 20MB、每批 20 个的本地校验；使用人工会话或同账号 draft API Key。
  - 新增 `append_evidence()`：追加前先读暂存列表；同名已存在且唯一则跳过，重复则拒绝；丢失响应时重新读列表确认后再决定，绝不盲目重传。
  - 新增 `clear_review_evidence()`：仅人工会话可 `DELETE /api/reviews/{batch_id}/evidence`，不自动调用。
- `upload.py`
  - `_evidence_files()` 收集批次输入中的 `source`、`docx`、`answers`、`explanations`、`attachment_NNN` 的 PDF/DOCX/PNG/JPEG，校验在批次目录内、无软链接、单文件 20MB、最多 20 个，并记录路径、大小、SHA-256。
  - 审核附件清单写入 `07_upload/plan.json`，因此人工批准与冻结摘要覆盖附件；`verify()` 重新校验附件未变化。
  - `submit_approved()` 在 `POST /api/uploads` 成功后逐个追加附件；至少一个附件才允许提交，否则拒绝。附件失败时在 `receipt.json` 保留暂存 ID 和已上传清单，下一次只对账续传，不重新创建暂存；确定性拒绝会记录 `attachment_rejected` 并停止盲目重试。
- `upload-api.md` 增加“审核附件（Word/PDF/图片）”章节，明确与题目 `attachments` 的边界及产物字段。
- 回归：`test_upload` 29 项、`test_upload_client` + payload/content 152 项；覆盖二进制头、文件编码、已存在去重、丢失响应重读列表、重复附件拒绝、清理接口人工会话、附件哈希绑定与失败续传。

## 没改什么 / 边界

- 题目 `attachments` 仍只允许图片/音频；审核附件走暂存附件接口，不能放进题目 JSON。
- 不自动确认上传、不发布、不清除审核附件；`clear_review_evidence` 只有人工会话显式调用才执行。
- 创建暂存仍要求人工会话；API Key 只能用于同账号有 draft 权限的追加。
- 未修改任何用户批次、manifest 或已冻结的历史上传。

## 验证

- `workflow.test_upload`：29 项通过。
- `workflow.test_upload_client`、`test_upload_payload`、`test_upload_content`：152 项通过。
- 合计 182 项通过。
- 全量 workflow：2047 项，2028 项通过、19 项跳过，失败/错误 0；耗时 87.471 秒（独立临时目录隔离测试锁；2 条依赖 ps 的用例因环境权限跳过）。
- 文档核对：`http://101.43.1.57/docs/#format` 的“上传审核附件”与“账号与 API Key”章节。

## 补充：附件失败后的重试

提交 `256760f` 修正失败态：暂存已创建但附件未追加成功时，回执状态为 `staged_attachments_pending`，上传状态显示为失败而不是“已暂存”，用户可再次点击“上传批次”重试；重试会复用同一暂存 ID，只对账并补缺失附件，不会重新创建暂存或重复提交题目。确定性拒绝状态为 `attachment_rejected`，需修正附件或权限后重新准备。

## 回退方式

回退本提交即可恢复仅创建暂存、不追加上传附件的旧行为；旧冻结计划需重新准备后才能上传。
