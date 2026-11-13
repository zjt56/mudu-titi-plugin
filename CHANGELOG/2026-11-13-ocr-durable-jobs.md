# OCR 异步任务：提交、持久回执、等待续查、结果复用与安全诊断

## 背景

用户要求「补齐 OCR 的调用功能、等待结果返回的功能」。此前 PaddleOCR jobs 的调用是单次进程内：`POST → 轮询 → 下载`，任务身份只存在于内存。

- 进程崩溃、后端重启或轮询超时后，已提交任务没有任何持久记录，只能再次提交，可能重复计费；
- POST 响应丢失时无法判断远端是否已接收，重试等于盲发；
- 结果下载中断时整卷重做；
- 等待只表现为阶段失败，诊断无法区分「任务仍在跑」和「真的失败」；
- 已成功的结果每次重新识别。

偏移修复（`b7535c6`）只补了轮询重试与 exit_code=6 的表面诊断，没有持久任务身份。

## 改了什么

- 新增 `app/workflow/ocr_job_store.py`：批次内持久回执（原子写 + fsync + 目录 fsync），OS 文件锁（进程退出自动释放，不按锁文件年龄清理），语义指纹（源字节摘要、endpoint/model 摘要、识别选项、页范围、任务类型；排除令牌与超时）。
- `paddle_jobs.py`：
  - `recognize(source, output_dir, *, config, pages=None, task='ocr', progress_callback=None)` 以 `output_dir` 为持久作用域，指纹稳定目录 `paddle-ocr-<fingerprint>/`；
  - POST 前原子记录 `submitting` 与随机 `batchId`，取得 `jobId` 立即落盘；
  - POST 结果未知（断线/超时/5xx/无 ID）记 `submission_unknown`，后续只用 `GET /jobs/batch/{batchId}` 对账：唯一匹配即恢复，空列表仍未知，多条为 `submission_conflict`；任何情况都不盲发第二次 POST；
  - 查询/下载各有三次有界重试与退避；`_DeadlineTransport` 以真实 socket 的绝对截止时间中断慢速/涓流响应（不依赖每次 read 的空闲超时），并兼容 Python 3.12 的 `HTTPSConnection` 签名；
  - 总预算耗尽但有 jobId 时抛 `waiting`（可续查），终态失败保留 job/phase/retryable 与回执上下文；
  - 成功前校验结果文件与源字节摘要，`succeeded` 回执只在验证后写入；后续调用本地复验完整即复用，不联网；缓存损坏但有 jobId 时续查恢复，不重新提交；
  - 回执只保存安全字段，不含令牌、签名 URL、远端原始响应；
  - `MD_OCR_RECONCILE_ONLY=1` + `MD_OCR_RECONCILE_BINDINGS` 提供 GET-only 防线：对账调用即使配置或回执指纹变化也绝不 POST。
- `pdf_ocr.py`：报告升级为 version 2，实时保存每源 `progress`、`updated_at` 与状态；等待不交付结果，任何真实失败优先；每源在可对账时写 `reconciliation`（`method=GET`、批 ID、指纹、源摘要）。
- `extract.py`：OCR 等待时抛 `OCRAcquisitionPending`，CLI 退出码 6 且报告 `status=waiting`，不写成功产物；失败仍阻断提取（含 Word 主源的配套 PDF）。
- `runner.py`：`ocr_wait_state()` 只认可本次运行后写入、覆盖全部注册 PDF、输入/路径/摘要当前、带 job ID 的等待报告；退出码 6 单独不作证据。`ocr_retry_after()` 把等待间隔限制在 5–300 秒。
- `auto.py` / `supervisor.py`：等待为 `waiting_external`，进入 pending 而非 failed；supervisor 按持久化的到期时间续查，不消耗模型冷却、不触发代码运维修复、不空转。提交结果未知时走有限次 GET 对账（`reconciling_external`），耗尽后保持失败，不做代码修复。
- `diagnose.py`：安全显示任务已提交/排队/运行/下载/等待续查，非阻断；等待证据失效或被替换时重新标为需处理，绝不沿用旧的良性等待。
- `ocr_backend.py`：受控错误码白名单（`waiting`、`submission_unknown`、`submission_conflict`、`receipt_corrupt`、`storage_error`、`job_not_found`、`job_expired` 等）+ 进度回调；任意异常不再序列化配置或凭据。
- 文档：`app/docs/extraction.md` 新增「异步任务提交、等待与恢复」。

## 没改什么 / 边界

- 不声称远端 exactly-once：只能做到本地不盲发、可对账恢复；服务端是否已创建任务只能由 batchId 查询证明。
- 不替换识别服务、不改 OCR 阅读顺序、不手工重排版面。
- 等待不是成功：等待期间不交付提取、不进入下游；空文字、仅原生文字层仍判失败。
- 不跨批次共享缓存（回执在批次内），不改历史批次、不自动上传。
- 本机 CLI OCR 后端（`python + script`）仍是单次子进程模型，本次持久恢复只覆盖 Paddle jobs。

## 验证

- `workflow.test_ocr_pipeline_recovery` 通过真实 HTTP 回环验证：等待→续查→成功只 POST 一次；未知提交→batch 对账恢复且不二次 POST；成功后再调用零网络。
- 真实服务端到端（本机私有 Paddle 配置，1 页合成 PDF）：提交 → `job_id=114707904062754816`、`state=done`，2.87 秒完成；再次调用 0.003 秒本地复用，页文字来自服务结果。
- 测试：`test_ocr_acquisition_recovery`、`test_ocr_pipeline_recovery`、`test_pdf_ocr`、`test_pdf_ocr_integration`、`test_ocr_backend`、`test_extract`、`test_paddle_jobs`、`test_ocr_job_store`、`test_paddle_jobs_recovery`、`test_ocr_wait_scheduling` 共 198 项（含修正两处过时断言后全绿）。
- 隔离副本全量 `workflow`（排除两个既有慢模块 `test_structure_number_inference`/`test_source_structure`）：**3312 项，16 失败、4 错误、21 跳过**（457.7 秒）。失败项与改动前基线副本逐项一致（audio_skip、meta_source_projection、metadata_source_name、question_identity_format、review_read_scope、structure_closure、pdf_lesson_structure、operations 相关、changelog 政策），均为既有问题；其中 changelog 政策两条已在本轮文档规范化后于主目录复验通过。
- 直接运行全量 discover 时，既有 `test_structure_number_inference` 模块在当前环境长时间 sleep 轮询（>20 分钟不前进，进程 0% CPU），未作为证据；该模块在基线与本次改动上表现相同，与本轮 OCR 改动无关。

## 回退

回退上述 OCR 模块与对应测试即可；批次内已生成的 `paddle-ocr-*/receipt.json` 与 `result.*` 为只读证据，可保留不影响旧逻辑。

## 版本控制

主目录在另一会话完成变基后干净同步 `origin/master`，本次改动直接落在 `master`。
