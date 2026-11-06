# 飞桨 OCR jobs 后端接入

插件 Host 提供 OCR 私有配置与完整测试接口。适配器使用 Python 3.9+ 标准库 `urllib.request`，不依赖 `requests`、Paddle SDK 或本地 GPU。不会自动安装软件、修改 profile 或更改本地 OCR 模型依赖。

## 配置接口

`GET /wf-plugin/ocr` 与 `POST /wf-plugin/ocr` 返回：

```json
{
  "ok": true,
  "config": {
    "enabled": false,
    "endpoint": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
    "model": "PaddleOCR-VL-1.6",
    "has_token": false,
    "useDocOrientationClassify": false,
    "useDocUnwarping": false,
    "useChartRecognition": false,
    "timeout_seconds": 300,
    "poll_interval_seconds": 2
  }
}
```

POST 使用 `Content-Type: application/json`，接受上述配置字段（不含只读 `has_token`）以及 `token`、`clear_token`。字段可部分提交；未提供的字段沿用已保存设置。空 `token` 保留现有 Token；`clear_token: true` 明确清除并屏蔽环境变量 Token。不得同时提交非空 Token 与 `clear_token: true`。

`enabled` 是 OCR 辅助总开关，默认 `false`。关闭时 `recognize()` 直接拒绝并给出中文提示；原生 PDF 读取、DOCX 解析和文字层提取不受影响。开启后文字提取仍是主路径，OCR 只补充质量不足的 PDF 页、图片型试卷和图片型 Word。开关状态写入同目录 `assist.json`，Host 通过 `MUDU_OCR_ASSIST_STATE` 指向该文件；工作流每次 OCR 调用重新读取，因此切换开关无需重启后端。独立工作流没有该状态文件时保持旧行为（存在配置即可用），也可用 `MUDU_OCR_ASSIST=0/1` 显式覆盖。

默认地址支持 HTTPS；为本地集成测试允许字面回环 HTTP（localhost、127.0.0.1、[::1]）。endpoint 必须以 `/jobs` 结尾，禁止 URL 凭据、查询参数、片段、编码路径或路径穿越。模型为非空字符串，三个开关为布尔值，`timeout_seconds` 范围 1..1800，`poll_interval_seconds` 范围 0.01..60。禁止额外未知字段。

Token 与配置分别保存在仓库外 `$DSH_HOME/plugin-state/md-upload-tiku/ocr/`（未设置 DSH_HOME 时使用用户目录下 `.dsh`）。目录权限 0700，文件 0600，原子写入；拒绝符号链接、硬链接和仓库内存储。公共接口只返回 `has_token`，不返回 Token 或私有文件路径。所有接口响应均带 `Cache-Control: no-store`。配置错误、存储错误的响应不包含原始提交内容。

只有保存设置才启用云端 OCR。Host 子进程环境仅传 `MUDU_PADDLE_OCR_CONFIG` 私有路径；工作流每次识别读取配置及 Token 文件，保存或清除 Token 后无需重启后端。尚未保存时继续使用原有 `MUDU_OCR_CONFIG` / 本地 `ocr-config.json`。独立工作流也可将 `MUDU_OCR_CONFIG` 指向私有 jobs 配置（backend=paddle_jobs，Token 通过 MUDU_OCR_TOKEN 或私有 token_file 提供），不要将含凭据的配置放入仓库。

## 完整测试接口

`POST /wf-plugin/ocr/test` 接受保存前的草稿，不强制先保存，也不受 `enabled` 开关限制，便于开启前验证配置。测试会提交真实 OCR 任务、轮询完成并下载 JSONL，不只是探测 URL。调用外部服务将发送指定文件并可能消耗服务额度。

扁平草稿示例（通过用户界面填写 Token；此处不包含凭据）：

```json
{
  "endpoint": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
  "model": "PaddleOCR-VL-1.6",
  "useDocOrientationClassify": false,
  "useDocUnwarping": false,
  "useChartRecognition": false,
  "timeout_seconds": 300,
  "poll_interval_seconds": 2,
  "file_url": "https://example.org/synthetic.png"
}
```

也支持 `{ "config": { ...草稿配置 }, "file_url": "..." }`，或使用 `file_path` 指向本地合成 PNG/PDF；两者必须且只能提供一个。嵌套 `config` 和顶层配置字段不能混用。可在草稿中提供临时 Token 或借用已存 Token。草稿及临时 Token 不写磁盘；Host 通过 stdin 传入所选 Python 子进程，不放入命令参数或日志。

成功响应 HTTP 200：

```json
{
  "ok": true,
  "message": "OCR 完整测试成功：已提交、轮询并读取识别结果。",
  "pages": [{"page": 1, "text": "...", "method": "ocr", "blocks": [], "images": {}, "outputImages": {}, "warnings": ["coordinates_absent", "remote_images_unverified"]}],
  "markdown": "...",
  "job_id": "...",
  "seconds": 1.2,
  "warnings": ["coordinates_absent", "remote_images_unverified"]
}
```

失败响应 HTTP 400：`{ "ok": false, "code": "...", "message": "..." }`。code 包括 invalid_config、storage_error、missing_token、python_unavailable、http_error、unsafe_redirect、network_error、timeout、job_failed、invalid_result。HTTP 错误还返回 `http_status`；若服务返回结构化 `errorMsg/errorMessage/message` 与 `errorCode/code`（允许 data/error 容器），则返回限定长度的 `error_message`（500 字符）和 `error_code`（128 字符）。这些字段去除实际 Token、Bearer 和 URL，服务其他字段、响应正文、响应头和重定向地址不会回传。脱敏具体提示也会附加到 message，便于区分鉴权、配额与格式错误。方法不匹配为 405；非 JSON 请求为 415。

## jobs 协议与工作流兼容性

- URL 输入：POST endpoint，JSON `fileUrl` / `model` / `optionalPayload`。
- 本地输入：POST endpoint，multipart `file` / `model` / JSON 字符串 `optionalPayload`。文件上限 200 MiB，仅支持 PDF/PNG/JPEG/WebP/BMP/TIFF；只发送固定安全文件名，避免泄漏本地目录。
- 提交与状态查询使用 `Authorization: Bearer ...`；`GET endpoint/{jobId}` 支持 pending/running/done/failed（状态字段 state 或 status）。
- done 后从 `data.resultUrl.jsonUrl` 下载 JSONL；下载请求不携带 Authorization，拒绝所有 HTTP 重定向。
- 逐行读取 `result.layoutParsingResults[].markdown.text/images` 与 outputImages，转为工作流 pages。完整测试仅返回图片引用，不额外下载；工作流 `recognize()` 则将图片保存到独立产物目录的 `media/`，使用程序生成的每页文件名并改写 Markdown 相对链接，`artifacts.images` 记录本地文件。文件名不采用服务端路径，拒绝路径穿越、SVG/HTML；下载不带 Authorization、拒绝重定向，最多 64 张、每张 20 MiB、总计 100 MiB，并共享总超时。失败在页与全局 warnings 留下 `media_download_failed`；保存成功仍须人工视觉核对，不代表图片归属或 OCR 正确性已验证。
- `pages` 选择在返回页上过滤，保留原始页号。API 未定义页面子集提交，因此服务仍处理整个文件；未请求的页不写入所选文本结果。
- 整个提交、轮询、下载共用总超时；POST 从不自动重试，超时或状态未知不能自动重复计费提交。
- 本地 OCR 配置、CLI 参数、模型锁与输出契约保持原状。PDF 原生提取仍调用原本本地 OCR 环境中的 PyMuPDF；未配置本地环境时使用当前 Python（需提供 PyMuPDF），不因启用云端 OCR而改为全卷 OCR。
- OCR 不产生虚构坐标、可信度或审核通过状态。已有结构闸门、视觉复核与人工批准边界继续有效。

## 验证

```bash
node --test backend/ocr-routes.test.mjs backend/question-bank-routes.test.mjs backend/question-bank.test.mjs
# 在 app 目录下：
python3 -m unittest workflow.test_ocr_backend workflow.test_paddle_jobs
python3 -m unittest workflow.test_agent_protocol workflow.test_agent_entry workflow.test_local_agent
```

回归通过本地 HTTP 协议服务器、真实 Python 子进程与合成小 PNG/PDF验证 URL、multipart、轮询、JSONL、配置私有权限、错误脱敏和本地兼容性，不包含真实用户文件或云端凭据。飞桨线上鉴权、额度与真实模型质量需另用私有 Token 和合成图片验证；本地协议回归不代表线上识别质量已通过。
