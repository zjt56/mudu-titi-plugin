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
| **扫描件/图片 OCR** | 外部 OCR 服务（如 PaddleOCR-VL） | **可选**，在 `app/workflow/ocr-config.json` 或 `MUDU_OCR_CONFIG` 配置；不配则只有 PDF/图片 OCR 不可用，docx 主流程不受影响 |
| **模型调用** | DSH 内可用的模型 | 面板里选择，凭证从 `~/.dsh/.credentials.yaml` 读 |

- Python 由插件**自动探测**（`WF_PYTHON` 环境变量 → 常见路径 → `PATH` 上的 `python3.12`/`python3`），找不到时面板会直接显示"未找到可用的 Python"及解决提示。
- 需要手动指定时：设置环境变量 `WF_PYTHON=/path/to/python3.12`。

## 上传支持的文件

- **只支持 `.docx` 与 `.pdf`**（另可带 `.mp3/.wav/.m4a/.ogg/.flac` 听力音频）。
- **不支持旧版 `.doc`**：它是二进制格式，无法无损解析（图片会丢）。请先在 Word/WPS 里"另存为 `.docx`"再上传。

## 能力

- **选择 harness 内模型（含第三方）**：面板下拉列出 base profile 里的全部模型（DeepSeek 官方 `deepseek-flash`/`deepseek-v4-pro`、Magpie 中转 GPT 系列、Codex 系列）。
- **分别设置三审模型**：一审(labels)/二审(knowledge)/三审(sample) 可各自指定；默认统一走 `deepseek-v4-pro`。
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
4. （可选）要识别扫描件：自备 OCR 服务并在 `app/workflow/ocr-config.json` 配置。

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

「模型与设置」采用左右布局：左侧是全局、阶段和两轮审核模型；右侧是题库 API 连接。

- 必填连接项：API Base（以 `/api` 结尾）、API Key、科目 ID（默认 `english`）。
- 默认连接 `http://127.0.0.1:8089/api`，需先建立本机 SSH 隧道；也接受 HTTPS。拒绝公网明文 HTTP、带账号密码的 URL 和重定向。
- 高级项：请求超时（默认 30 秒）与可选知识点版本 ID。上传方式和目标属于批次资料，不作为全局连接参数。
- 密钥输入框不回显已保存值；留空保留，填写新值替换，「清除密钥」显式移除。
- 私有文件位于 `$DSH_HOME/plugin-state/md-upload-tiku/question-bank/`（默认在用户的 `.dsh` 下），目录权限 0700，文件权限 0600；不写入项目、题目 JSON、模型提示词或本地浏览器存储。这是本机文件权限保护，不宣称加密存储。
- 后端启动时通过 `MUDU_UPLOAD_CONFIG` 和 `MUDU_API_KEY` 注入现有上传客户端。保存不会中断现有任务，新凭据在后端下一次启动时加载；面板连接测试立即使用当前填写值。
- 「测试连接」只 GET 当前知识点版本目录，不提交题目，也不能据此判断具备 `draft` 上传权限。
- 云端提交仍遵循预检、冻结及人工批准流程，只写入上传暂存。确认入库和发布由远端人工完成；本设置面板不会上传、确认或发布题目。

## 注意

- 临时 profile 从 `wf-deepseek`/`wf-deepseek-fast` 复制，所以**可选模型以这两份 profile 为准**；新增模型要同步加到 profile。
- 模型供应商凭证仍从 `~/.dsh/.credentials.yaml` 读取注入，不打印；题库 API Key 按上文的私有配置规则保存。
