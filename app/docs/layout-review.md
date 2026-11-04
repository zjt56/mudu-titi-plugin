# 整页版面 OCR 复核辅助

`workflow.layout_review` 为结构疑点提供可续查的整页 OCR 候选证据。它独立于原卷提取与结构闸门，不写批次台账，不登记阶段完成，不自动修改疑点的 `resolved`。无需 GPU、厂商 SDK 或导入本机模型。

## 配置与接口

使用 ocr-workbench 已配置的 HTTP 服务：

- 环境变量 `OCR_BASE_URL` / `OCR_API_KEY`；或者 `OCR_CONFIG` 指向共享 HTTP 配置。
- `--config <HTTP配置>` 优先选择配置文件，环境变量覆盖其中的 `base_url` / `api_key`。
- `--client <skill目录>/scripts/ocr.py` 只读取客户端同目录的 HTTP 配置；也支持环境变量 `OCR_WORKBENCH_CLIENT`。不会执行客户端或重复 health。
- HTTP 配置格式为 `{"base_url": "https://<服务地址>", "api_key": "<由操作者配置>"}`。不要把真实配置放进 Git、公开任务包或命令输出。

这与 `workflow.ocr_backend` 的 `MUDU_OCR_CONFIG` 是两个接口。后者配置 Python 和本地 CLI，提供文本 OCR；其空 `blocks` 不算版面识别结果。新入口不会把本地 CLI 配置当 HTTP 配置，也不会降级到 UniMERNet/texify 来冒充整页证据。OpenAI 兼容接口只返回 Markdown，不能满足这里的页码与版面块证据要求。

提交使用 `POST /api/jobs`，固定 `engine=paddle`、`layout=true`，一次只传一个 PDF/整页图。续查使用 `GET /api/jobs/{id}`。接口以 ocr-workbench skill 的 `references/api.md` 为依据：完成结果来自 `result.pages[].page/markdown/blocks`，块含 `type` 和 `bbox`。本入口不主动 health；操作者可沿用已取得的服务状态。Python 调用可传 `health=已有响应`，由纯函数 `document_engine_readiness(health)` 判定：Paddle 为 error/disabled/stopped 或在明确的引擎清单中缺失时不上传，落盘 `not_submitted/failed/needs_review`；idle/loading 允许冷启动排队，公网隐藏引擎时为 unknown。检测故障后恢复的服务若需重新提交，选择新输出目录。引擎未就绪、报错、缺布局块均保留待复核。不要为了通过检查改用公式引擎。

## CLI 调用

从仓库根目录执行，`python` 可换成环境中的 `python3`。输出目录应选专门的辅助证据目录；每轮原卷/结构变化后选新目录。

```bash
# 提交一份原卷 PDF，记录原卷和结构/提取快照的哈希。
python -m workflow.layout_review --config <HTTP配置> submit <原卷.pdf> \
  --out-dir <辅助证据目录> --expected-pages 8 \
  --structure <结构候选.json> --extraction <提取证据.json> \
  --source-text <原始正文.txt>

# 只查询一次，稍后继续执行同一命令；不会重新上传。
python -m workflow.layout_review --config <HTTP配置> resume \
  <辅助证据目录>/layout-review.json

# 只有确定是一张完整原页时才作此声明；不能用于题图、截图局部或嵌入图片。
python -m workflow.layout_review --config <HTTP配置> submit <完整页.png> \
  --whole-page --out-dir <另一辅助目录> --page-map <页码映射.json>
```

`--page-map` 是服务页码到原卷页码的 JSON 对象，如 `{"1": 7}` 表示上传的是原卷第 7 页整页图。映射必须是正整数且不重复；服务页码、原卷页码都保留，不能静默重编号。一旦提供映射，就应覆盖此次返回的所有服务页；缺失映射的页将记为不完整证据，原卷页码留空。该输入是局部页证据，不能据此宣称整卷已核对。`--expected-pages` 是此次提交件的页数，不是映射后的最大原卷页码；建议从可信的渲染/原生 PDF 检查取得。未提供页数时无法发现服务截断末尾页面，输出始终 `coverage_verified=false`。

DOCX 不能直接上传，也不能把 DOCX 媒体目录的图片 OCR 当成整页版面证据。先用可信的 Office 渲染器导出完整 PDF 或完整页图，再显式绑定：

```bash
python -m workflow.layout_review --config <HTTP配置> submit <原卷.docx> \
  --rendered-source <该原卷渲染.pdf> --whole-page \
  --out-dir <DOCX辅助目录> --expected-pages 8 --structure <结构候选.json>
```

调用者负责确认渲染件来自该 DOCX、字体和分页符合原件，并保留渲染工具/版本/告警。此模块保留原 DOCX 与提交渲染件各自的 SHA-256；`--whole-page` 是操作者的输入范围声明，程序不会把它解释为视觉审核已通过。若只提供一张页图，用页码映射明确其原卷位置。换渲染件、换原卷、换结构后建立新证据目录。

CLI stdout 只返回状态、job_id 与证据可用性，不输出服务 URL、密钥或本机配置。退出码 0 表示已取得可用版面候选，**不表示结构通过**；2 表示仍需查询、补证或修复输入。JSON 的 `status` 始终为 `needs_review`，`passed=false`。

## 异步任务、超时与防重复

提交前先原子写入 `layout-review.json`，标记 `request_started`，取得 id 后立即落盘。重复执行 `submit` 并使用同一输出目录会返回已存状态，不再 POST，也不隐式查询。

- 返回 504 且带 `job_id`：保留原 id，继续 `resume`。
- 查询超时/HTTP 错误：保留 id、已有原始响应与页证据，继续查询同一任务。
- 上传连接断开且没有收到 id：记 `submission_unknown`，禁止自动重传。先到服务端核对是否已接单，找到原 id 后使用 `resume <报告> --job-id <已核对的id>` 绑定续查。不能把猜测的 id 当成恢复依据。
- 服务 `error` / `interrupted`：保留失败及原始结果，`needs_review`；不自动重试上传。确认确需重做后，操作者选择新输出目录开始新的独立尝试。
- 并发用独占锁防止同目录重复提交。进程崩溃可能留下 `layout-review.json.lock`；先核对服务与已存状态，确认没有提交在途再由操作者移除锁。程序不会按时间自动清锁或重传。

标准库 HTTP adapter 不包含代理失败后的 POST 自动重试。网络错误原文可能含连接信息，报告只存安全错误代码。原始 JSON 响应独立保存；若服务回显凭据，凭据字段和已知 key 字符串会被遮盖，其余响应字段保留。原始响应可能含原卷内容、服务路径或识别资产引用，只应按私有证据管理。

## 证据与结构疑点

`layout-review.json` 保存：

- 原卷与提交件路径/SHA-256；可选结构、提取 JSON、原始正文各自的哈希绑定。
- `job_id`、提交状态、服务状态、请求引擎，以及 `raw_results` 的独立响应文件路径/哈希。
- `pages[]` 的服务页码、原卷页码、Markdown、原始 blocks。缺块、框不合法、页码重复、页序缺失、已知页数不符、实际引擎不是 Paddle 都记为不完整证据。
- `targets[]` 复制结构 `unresolved`，提供稳定的本报告内 target_id 与页候选引用；不声称块已与某道题精确匹配。

结构疑点有显式 `page/pages` 时优先使用；否则可按原始正文中的 `[PAGE n]` 标记把 `line/source_line` 关联到页。只有行号确实指向该原始正文时才能传 `--source-text`，不能拿清洗/删行后的文本套原行号。没有可靠页定位时保留 `association=unmapped`，不猜页、不从嵌入图片的顺序推页码。页已定位但服务未返回该页时为 `page_missing`。证据引用的 `block_indexes` 是该候选页上的索引，不能当作疑点已命中某个区域。

续查前重算所有绑定文件的哈希；任一变化时标记 `stale`，不继续请求服务。旧 OCR 可以保留供比较，但不能用于当前结构的放行。报告不是防篡改签名，外部正式审核仍应重新核对哈希与证据。

## 整版核对与判定边界

取得 `layout_available` 后，复核者仍需打开提交 PDF/原页图，与 OCR 的每页 blocks 和原始结果逐项核对：

1. 页数、原卷页码与渲染件分页一致；跨页材料和多栏阅读顺序完整。
2. 大题标题、题号、作答说明、选项及空位都没有串到相邻题；题组共享材料、导语和成员边界正确。
3. 表格行列、合并单元格、题图及其文字属于正确题目；页眉、页脚、答案附录和装饰内容没有混入题干。
4. 对每个结构疑点留下原页区域、具体结论和依据。OCR 不能证明逐字可靠、题图归属、题号覆盖或听力内容正确；辨认不清继续待复核。

版面 blocks 的坐标原样保留，服务未声明单位/尺度时不能擅自解释为 PDF points，也不能与本地原生 PDF 框直接作几何等同比较。blocks 的存在仅表示服务提供了版面候选，不能证明跨栏顺序、图文绑定、表格网格都正确。

此模块不接受审核签名、不制造 `resolved=true`，也不签署人工视觉核对。结构闸门与逐题核对如何采用这些证据，仍由各自的有效契约决定。

## 给提取/结构入口的集成建议

公开 Python 接口：

```python
from workflow.layout_review import start_review, resume_review

report = start_review(
    source, auxiliary_dir,
    config=http_config,             # 或由环境变量/HTTP配置解析
    structure_path=structure_path,
    extraction_path=extraction_path,
    source_text_path=original_text_path,
    expected_pages=page_count,
)
# 异步调度下一轮：仅 GET，可在超时后继续同一 id。
report = resume_review(auxiliary_dir / "layout-review.json", config=http_config)
```

还可独立调用 `build_review_targets(structure, source_text=raw_text)`，只做疑点页关联，无网络副作用。测试或外部客户端可注入 `client`，实现 `submit(Path) -> (http_status, JSON)` 与 `status(job_id) -> (http_status, JSON)`；注入客户端同样必须保证 POST 不自动重试，并负责清除返回 JSON 中的凭据。

建议原卷提取器在产生 `OCR_VISUAL_REVIEW`、题组边界或图文归属疑点后，显式把原卷/渲染件与候选结构送入此辅助入口。把证据路径、哈希、job_id、页候选引用附到疑点的辅助字段；先检查 `evidence_state`，失败/不完整/未知页时保留疑点。取得候选后再按原卷进行实际核对，不以 OCR 的 `done` 自动清除 blocker，也不把本报告的存在当成机器闸门通过。

### runner 的阻断调度 helper

`workflow.layout_dispatch.prepare_layout_fallback(batch_dir, manifest, gate_report)` 将结构阻断与异步 OCR 连接起来。传入 `passed=true` 的闸门报告时返回 `not_required` 且无写入；其余情况生成 `07_review/layout-request.json`，保留原卷/结构/提取/原始正文哈希、闸门报告摘要，以及合并结构疑点和闸门 blocker 的页候选 targets。返回 `request_path`、`report_path`、`job_id`、`dispatch_state`，供 runner 写进自己的阻断报告；helper 不更新 manifest、闸门结论或结构。

如果配置了 `OCR_CONFIG`、`OCR_BASE_URL` 或 `OCR_WORKBENCH_CLIENT`，PDF 原卷自动使用上述 HTTP adapter 提交一次，证据放在 `07_review/layout-ocr/`。首次调用仅 POST，不阻塞等待；之后 runner 再调用时验证同一输入绑定并 GET 同一个 id 一次。缺配置或配置错误返回 `unavailable`，上传超时无 id 返回 `submission_unknown`，服务失败返回 `failed`，均保留 `needs_review`；不因 helper 故障中止 runner，也不重新上传。已有报告的 job_id 即使当前缺配置也继续保留。旧报告、疑点绑定或源文件变化后，helper 不会自动选择新目录绕过防重复保护，应核对已有任务并显式发起新的独立尝试。

DOCX 只有显式登记的完整 PDF 才会自动上传。可由操作者的正式输入登记流程提供 `manifest.inputs.layout_pdf` 或 `manifest.inputs.rendered_source`，格式为 `{"path": "渲染件.pdf", "sha256": "<实际摘要>"}`；绝对路径直接使用，相对路径从 batch_dir 解析。原卷与渲染件都只读，若注册摘要存在则必须匹配。未提供渲染件返回 `requires_render` 与 CLI/输入登记步骤；图片作渲染件不会自动上传，因为无法凭一个图片路径判断完整分页或原卷页码。完整页图仍可通过独立 CLI 配合 `--whole-page`、`--page-map` 明确提交。

```python
from workflow.layout_dispatch import prepare_layout_fallback

# 结构闸门报告已产生，且 passed 为 False 时由 runner 调用。
layout_request = prepare_layout_fallback(batch_dir, manifest, gate_report)
# 由 runner 保留 layout_request 的路径和状态；它不构成结构通过。
```

helper 的网络路径直接调用独立 adapter，未重复执行 health，也不会依赖厂商客户端默认重试。现有实际 Paddle 引擎异常仍是限制；离线调度测试模拟真实 HTTP 响应，不能据此宣称远端引擎已恢复或整卷已核验。

本次实现没有修改 `extract.py`、`structure_gate.py` 或 runner，没有操作真实批次或 manifest。

离线验证：`python -m unittest workflow.test_layout_review`。测试覆盖 async 提交/续查、504、连接超时、无 id 的上传歧义、防重复、失败引擎、缺块、页覆盖、DOCX 渲染边界、哈希失效和结构疑点页关联；不调用真实服务，也不代表已完成真实原卷整版核验。
