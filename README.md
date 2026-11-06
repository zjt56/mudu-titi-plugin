# md upload tiku · DSH 插件（英语录题与题库上传）

把上传工作流做进 **DSH 原生插件面板**。**唯一前端就是 `lib/client.js`**（React 面板，运行在 Harness 里）：所有显示、查看、修改、操作都在插件面板完成，项目里**没有独立的浏览器页面 / 静态站点 / iframe 工作台**。`app/` 只是无界面的 Python 工作流 + REST API 后端，试卷数据保存在本项目目录下（`workflows/`、`inbox/`），不依赖任何外部仓库。模型路由通过 `MUDU_AGENT_CONFIG` + 插件自有 `wf-router.sh` 注入实现。

## 数据目录

- 新装机器：数据默认放在 **`$DSH_HOME/wf-plugin-data/`**（即 `~/.dsh/wf-plugin-data/`）下的 `workflows/`（批次）、`inbox/`（上传暂存）、`.workbench-trash/`（可恢复归档），升级/重装插件不会丢数据。
- 老用户兼容：如果插件安装目录里**已有** `workflows/`，会继续沿用原位置，不会让数据"凭空消失"。
- 显式指定位置：设置环境变量 `WF_DATA_DIR` 即可覆盖以上两种行为。
- 以上目录都在 `.gitignore` 忽略范围，不随代码提交。

## 依赖（给别人用请先看这段）

| 能力 | 需要什么 | 说明 |
|---|---|---|
| **docx 解析（主流程）** | 只需 **Python 3.9+** | 纯标准库实现，**零 pip 依赖** |
| **PDF 解析** | Python + **PyMuPDF** | `pip install pymupdf`；装了就能解析纯文本 PDF |
| **扫描件/图片 OCR** | 飞桨 jobs API 或原有本地 OCR | **默认关闭，可选开启**：在「模型与设置 → OCR 识别」打开总开关；标准库联网无需 requests。关闭时只走文字层/原生解析，扫描件会提示先开启 |
| **模型调用** | DSH 内可用的模型 | 面板里选择，凭证从 `~/.dsh/.credentials.yaml` 读 |

- Python 由插件**自动探测**（`WF_PYTHON` 环境变量 → 常见路径 → `PATH` 上的 `python3.12`/`python3`），找不到时面板会直接显示"未找到可用的 Python"及解决提示。
- 需要手动指定时：设置环境变量 `WF_PYTHON=/path/to/python3.12`。

## 上传支持的文件

- **只支持 `.docx` 与 `.pdf`**（另可带 `.mp3/.wav/.m4a/.ogg/.flac` 听力音频）。
- **不支持旧版 `.doc`**：它是二进制格式，无法无损解析（图片会丢）。请先在 Word/WPS 里"另存为 `.docx`"再上传。

## 能力

- **选择 harness 内模型（含第三方）**：面板下拉列出 base profile 里的全部模型（DeepSeek 官方 `deepseek-flash`/`deepseek-v4-pro`、Magpie 中转 GPT 系列、Codex 系列）。这里的「DeepSeek 官方」是 Harness 里填 API Key 的官方适配器（`deepseek-official`，读 `DEEPSEEK_API_KEY`），不是账号/订阅模型。
- **默认模型就是所有任务用的模型**：选中后，各阶段与两轮审核默认全部走它；只有你在「各阶段模型 / 两轮审核模型」里显式指定，才会覆盖对应环节。配置缺失或路由损坏时直接报错，不会静默换用其他模型。
- **自定义内容**：可编辑三审提示词、各阶段补充、审核口径（`config/prompts/*.md`、`config/review-policy.json`）。

## 原理（非侵入）

工作流（`runner`/`auto`/`review_rounds`）通过 `load_config()` 读 `MUDU_AGENT_CONFIG` 指定的配置。
本插件生成 `backend/.local-agent.effective.json`（`provider=command` → `wf-router.sh`），
由 `wf-router.sh` 读 `config/model-router.json`：

1. 从提示词头部识别 `Stage: <name>` 与（审核时）`ROUND: <labels|knowledge|sample>`；
2. 按路由器选出 provider/model；
3. 复制 base profile 为一次性临时 profile，仅改 `agent-default-model`；
4. 注入对应 provider 凭证（`DEEPSEEK_API_KEY` / `MAGPIE_GATEWAY_KEY`，从 `~/.dsh/.credentials.yaml` 读）；
5. 调 `dsh --profile <临时> -`，返回结果后删除临时 profile。

## 目录

```
插件版/
├── lib/client.js       唯一前端：DSH 原生面板（main + 侧边栏 + 设置）
├── lib/index.js        宿主：后端进程管理、配置读写、模型清单、REST /wf-plugin/*
├── app/workbench/server.py   纯 REST API 后端（无页面、无静态文件服务）
├── app/workflow/       工作流阶段、闸门、审核协议（无界面）
├── backend/wf-router.sh    模型路由 wrapper（按 Stage/ROUND 选模型）
├── backend/local-agent.plugin.json   插件生效配置模板
├── config/model-router.json   模型路由（可改）
├── config/review-policy.json  审核口径（可改）
├── config/prompts/*.md    提示词（可改）
├── locale/ icon.svg
```

宿主只把 `/wf-plugin/api/*` 透明代理到 Python 后端；面板通过这套接口读写数据，不加载任何 HTML 页面。

## 安装（给同事）

### 前置条件

1. 已安装 **DeepSeek Harness**（DSH）。
2. 已安装 **Python 3.12**（3.9+ 亦可）。macOS 可用 `brew install python@3.12`，或从 python.org 下载。
3. （可选）要解析 PDF：`pip install pymupdf`。
4. （可选）要识别扫描件：在「模型与设置 → OCR 识别」配置飞桨 jobs API 和私有 Token；或继续使用 `app/workflow/ocr-config.json` 的本地 OCR。

### 安装步骤

```bash
# 1) 获取插件（私有仓库，需要相应权限）
git clone <本仓库地址> md-upload-tiku
cd md-upload-tiku

# 2) 安装到 DSH profile
dsh plugin --profile <你的 profile> add "$(pwd)"
```

也可以让 DSH 的 `plugin_manager`（install_bundle）安装，目标是本目录的**绝对路径**。

### 安装后

- **重启 Harness**，侧边栏出现「录题系统」入口。
- 首次打开如果提示"未找到可用的 Python"，按提示装 Python 3.12，或设置 `WF_PYTHON` 后重启。
- 上传一份 `.docx` 试卷验证主流程即可，**不需要**配置 PDF/OCR 也能正常录题。

### 数据在哪

- 默认 `~/.dsh/wf-plugin-data/`（老用户若插件目录内已有 `workflows/` 则沿用原目录）。
- 面板 `/wf-plugin/health` 会返回实际使用的 `dataRoot` 与探测到的 `python`，排查问题时先看这个。
- 后端默认用 `8791` 端口；若被本机其他程序占用，会自动顺延到下一个空闲端口（面板无感知）。需要固定端口时设置环境变量 `WF_WORKBENCH_PORT`。

## 安装（本机开发）

在 DSH 里用 `plugin_manager`（install_bundle，目标为 `插件版/` 绝对路径），或：

```bash
dsh plugin --profile wf-deepseek add /path/to/插件版
```

安装后重启 Harness，侧边栏出现「录题系统」入口，打开即面板。

## 题库连接设置

「模型与设置」左侧提供四项分类导航：大模型配置、题库 API、OCR 识别、听力切分；右侧只显示当前板块。已经访问的板块保持挂载，切换分类会保留尚未保存的输入。

- 必填连接项：API Base（以 `/api` 结尾）、API Key、科目 ID（默认 `english`）。
- 默认连接 `http://127.0.0.1:8089/api`，需先建立本机 SSH 隧道；也接受 HTTPS。拒绝公网明文 HTTP、带账号密码的 URL 和重定向。
- 高级项：请求超时（默认 30 秒）与可选知识点版本 ID。上传方式和目标属于批次资料，不作为全局连接参数。
- 密钥输入框不回显已保存值；留空保留，填写新值替换，「清除密钥」显式移除。
- 私有文件位于 `$DSH_HOME/plugin-state/md-upload-tiku/question-bank/`（默认在用户的 `.dsh` 下），目录权限 0700，文件权限 0600；不写入项目、题目 JSON、模型提示词或本地浏览器存储。这是本机文件权限保护，不宣称加密存储。
- 后端启动时通过 `MUDU_UPLOAD_CONFIG` 和 `MUDU_API_KEY` 注入现有上传客户端。保存不会中断现有任务，新凭据在后端下一次启动时加载；面板连接测试立即使用当前填写值。
- 「测试连接」只 GET 当前知识点版本目录，不提交题目，也不能据此判断具备 `draft` 上传权限。
- 云端提交仍遵循预检、冻结及人工批准流程，只写入上传暂存。确认入库和发布由远端人工完成；本设置面板不会上传、确认或发布题目。

## OCR 识别设置

- **总开关默认关闭**：关闭时只使用文字层/原生解析，OCR 完全不参与，英文文字卷和 DOCX 走原路径。扫描件、图片型 PDF 或需要公式/版面识别时，在「OCR 识别」打开「启用 OCR 辅助识别」。
- 开启后文字提取为主、OCR 补充：文字 PDF 只对质量不足的页 OCR；扫描 PDF/图片由 OCR 识别；图片型 Word 补充图中文字。开关即时生效，不需要重启后端。
- 使用飞桨 `PaddleOCR-VL-1.6` jobs API，支持文档方向分类、去扭曲、图表识别、总超时和轮询间隔。
- Token 单独保存在仓库外私有配置中，界面只显示是否已保存；留空保留，明确清除才移除。保存后工作流按次读取，新 Token 无需重启后端。
- 「完整测试」支持草稿配置与 URL 或本地文件，执行真实提交、轮询和结果下载；无需先保存，也不受总开关影响。建议仅使用合成小图片测试，可能产生服务额度消耗。
- REST schema、错误脱敏、依赖和兼容性说明见 [OCR jobs 接口说明](app/docs/ocr-jobs.md)。

## 模型路由

- 面板选择写入 `config/model-router.json`；工作流每次调用 `backend/wf-router.sh`，按 `Stage` / `ROUND` 取默认或指定条目，只把该条目里的 provider/model 写进临时 profile。所有路径都走这一条：答案、题型、解析、知识点、装配、两轮审核、结构 AI 复核。
- 没有“换一个模型”的兜底：路由配置读不到、缺默认模型或条目无效时直接失败并提示重新选择，绝不静默改用其他模型。
- 修改默认模型后，下一次模型调用即生效，不需要重启后端；已经启动的分片继续用它启动时的路由。`config/model-router.json` 的 `stages` / `review.rounds` 为空时全部使用默认。
- 并发：插件生效配置 `part_concurrency` / `review_part_parallel` 默认 16，可在 `backend/local-agent.plugin.json` 调整，上限 64。官方 API 支持更高并发，实际限制是本机同时运行的 dsh 进程与内存。

## 注意

- 临时 profile 从 `wf-deepseek`/`wf-deepseek-fast` 复制，所以**可选模型以这两份 profile 为准**；新增模型要同步加到 profile。
- 模型供应商凭证仍从 `~/.dsh/.credentials.yaml` 读取注入，不打印；题库 API Key 按上文的私有配置规则保存。
