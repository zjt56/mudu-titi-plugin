# md-upload-tiku · 英语录题与题库上传插件

运行在 **DeepSeek Harness（DSH）** 中的英语试卷录题、审核与题库上传插件。

将一份或多份 Word / PDF 试卷（可附答案、解析与听力音频）整理为结构化、可审核、可上传的题目批次，自动完成 **19 个本地阶段**（提取原文 → 结构确认 → 前置判型 → 并行填充 → 汇总装配 → 本地体检 → 内容审核 → 格式检查），并在工作台提供 **试卷审核 / 题型审核**、原卷双栏对照、内容编辑、独立 AI 大意/解析补充与差异核对。**远端目录写入、上传暂存与发布始终需要人工明确确认，插件绝不自动提交题库。**

- **原生前端**：[`src/client/`](src/client/) 源码片段经 [`tools/build-client.mjs`](tools/build-client.mjs) 构建生成 [`lib/client.js`](lib/client.js)（DSH 侧边栏原生面板）
- **插件宿主**：[`lib/index.js`](lib/index.js)（后端生命周期管理、热更新排空重启、配置与路由桥接）
- **本地后端**：[`app/workbench/server.py`](app/workbench/server.py)（纯 REST 服务，不含网页）
- **阶段契约**：[`app/workflow/contracts.py`](app/workflow/contracts.py)（19 阶段状态与产物交接唯一来源）

---

## 核心特性

| 模块 | 能力说明 |
|---|---|
| **零人工本地流水线** | 19 阶段自动推进，按依赖并行填充；`validate` 后非听力审核分片立即与听力并行，仅听力分片等待转写；支持 `scoped-v1` 分节重号与无号题独立定位。 |
| **原文保真优先** | 题干、选项、材料、表格严格切片自原卷；来源优先级固定为 **Word > PDF/OCR > 空占位**，不编造、不跨材料借用。 |
| **灵活大模型路由** | 基于 Node 原生 [`backend/wf-router.mjs`](backend/wf-router.mjs) 跨平台路由，支持全局默认、各子 Agent、**文章大意/答案解析**与审核分别指定模型，并隔离临时会话防内存膨胀。 |
| **双视图工作台与并发编辑** | 提供 **试卷审核** 与 **题型审核**（按题型分页与键盘跨页）、Word 原卷双栏对照、字段级乐观并发编辑及独立「AI 文章大意 / AI 解析」补充，互不阻塞主流程。 |
| **差异是产出，不是卡点** | 一审全量与原标签的差异完整落入人工审核队列（兼容历史二审），不自动改标签、不伪造通过，也不阻断手动上传；仅确定性硬校验失败才构成阻断。 |
| **AI 运维分型自愈与稳定性加固** | 按故障分型选最小解法（外部认证等待恢复、规则变更直重跑、单题重生成、结构化修复、代码崩溃才隔离修复）；内置运行快照与安装前活跃批次互斥、只读前置诊断、Review 分片级进展监控与六维批次健康统计。 |
| **严格上传安全边界** | 本地硬校验通过且人工确认后，才执行远端答案考点注册、目录刷新、冻结载荷与专用上传 Key 暂存；凭据与真实试卷绝不入库。 |

---

## 快速开始

### 环境要求

- **DeepSeek Harness（DSH）**
- **Node.js** ≥ 18
- **Python** ≥ 3.9（推荐 3.12）
- **ffmpeg / ffprobe**（仅处理听力音频时需要）

### 安装为 DSH 插件

```bash
git clone <repository-url> md-upload-tiku
cd md-upload-tiku
dsh plugin --profile <profile-name> add "$(pwd)"
```

也可以直接在 DSH 插件管理器中添加本目录的绝对路径。安装后重启 Harness，侧边栏会出现 **「录题工作流」**。

### 首次初始化检查

打开 **「录题工作流」→「模型与设置」→「初始化引导」**，按需确认：

| 检查项 | 必需性 | 说明 |
|---|---|---|
| Python 运行时 | 必须 | 驱动工作流引擎与本地 REST 工作台 |
| 默认大模型 | 必须 | 默认自动跟随本机 Harness 当前选中模型，无需另填密钥 |
| ffmpeg / ffprobe | 听力时必须 | 音频切段、时长提取与格式转换 |
| 听力运行时与模型 | 按需 | WhisperX 转写与锚点切分（点击安装后才下载，不随插件打包） |
| OCR 识别 | 按需 | 扫描件、图片型 PDF 与浮动图片辅助识别；普通文字卷无需配置 |
| 题库连接 | 上传时需要 | 读取远端目录、注册答案考点与创建上传暂存 |

---

## 支持的输入格式

- **`.docx`**（推荐主格式）：优先提取 Word 原生文字、表格、图片与排版证据。
- **`.pdf`**：优先读取文字层，图片页或版面不确定时按策略触发 OCR。
- **`.mp3 / .wav / .m4a / .ogg / .flac`**：可选听力音频。
- 不支持旧版 `.doc`，请先另存为 `.docx`。

> Word 与 PDF 同时提供时始终以 **Word 原文为准**；PDF/OCR 仅辅助版面层级与缺失兜底，不覆盖已有 Word 内容。

---

## 19 阶段工作流

```text
extract          原卷复合提取（Word 原生 / PDF 文字层 / 按需 OCR）
structure_gate   结构自动确认（题号覆盖、分节边界、去噪、答案分离）
preassemble      前置批次组装（含目录题型 / 子类 / 词数判定）
types            题型交接校验（先于后续填充完成）
audio            听力切段 / 转写     ┐
answers          答案填充            │
explanations     原卷解析与大意抄录   ├ 按依赖并行推进，听力仅阻塞审核
knowledge        知识点标注（当前暂停教材匹配） │ 原卷无解析/大意时保留「无」占位
content_snapshot 画线前内容快照               │
meta             难度与分类                   │
underlines       画线标记与候选消歧           ┘
assemble         汇总装配与固化（消费画线视图，确定性合并点）
validate         本地自动体检（内容/结构/来源/标签硬校验）
review           内容审核（一审全量 + 缺证据局部补审，非听力与听力并行）
review_gate      审核放行判断（差异待人工不阻断手动上传）
format_gate      最终上传格式与平台适配检查
upload_gate      上传人工确认
upload           写入远端上传暂存
publish_gate     发布人工确认
```

补充说明：
- **书面表达**不按客观题严格校验：允许无固定答案或范文，缺项记「无」正常放行。
- **Harness 原生内容子智能体**：宿主在线时，结构确认、前置判型、复杂答案/解析匹配与知识点标注可按需检索目录与白名单图片，再交确定性验收器校验（详见 [`app/docs/harness-content-agents.md`](app/docs/harness-content-agents.md)）。

---

## 工作台：内容编辑与独立 AI 补充

在 **「试卷审核」** 页面中，可以对已装配批次进行精细校对：

- **人工编辑与防覆盖**：点击内容预览卡进入编辑弹窗，可修改文章大意、答案与逐题解析；保存时携带字段级 `expected` 原值基线，多会话并发编辑互不覆盖，同字段冲突会返回可操作提示。
- **按需 AI 文章大意 / AI 解析**：
  - 仅针对缺失、占位或已带 AI 标记的符合条件题目生成简明内容（≤50 字并自动标注），绝不覆盖原卷已有内容。
  - **完全独立于主上传流水线**：拥有独立批次任务锁与有界分片并发，不抢占主流程全局模型锁；落盘前复验题目输入指纹（答案/题干/选项/来源绑定），跳过生成期间已变更或已人工编辑的条目。
  - 若同批次主流程恰好正在写盘，已生成结果会自动暂存并在下次点击时免重跑直接落盘；支持弹窗实时展示当前调用模型、批次删除保护与热重启在途感知。

---

## 模型与外设配置

### 1. 大模型配置（`模型与设置 → 大模型配置`）

提供 4 组独立配置卡片，修改即时生效且自动保存：

1. **默认模型（全局）**：留空时自动跟随本机 Harness 当前模型；切换全局默认时，未单独指定的项同步跟随。
2. **各子 Agent 模型**：可为答案提取、听力切分、题型判定、原卷解析抄录、知识点标注、填充汇总、审核、AI 运维分别指定模型。
3. **文章大意、答案解析模型**：专为工作台按需触发的「AI 文章大意」（`roles.ai_summary`）与「AI 解析」（`roles.ai_explanation`）提供两个独立下拉框，均可跟随全局默认或分别指定更快的模型。
4. **两轮审核模型**：可分别为一审与二审抽样指定独立模型。

- 本机配置保存在 `~/.dsh/plugin-state/md-upload-tiku/model-router.json`，升级或重新检出仓库不会覆盖个人选择。
- 仓库 [`config/model-router.json`](config/model-router.json) 仅为无默认模型的空模板；一旦显式指定某模型，配置损坏直接报错，绝不静默切换。

### 2. OCR 与听力

- **OCR（`模型与设置 → OCR 识别`）**：支持 `auto`（默认，按需调用）、`on`（强制启用）、`off`（仅用原生文字层）三态策略，对接 PaddleOCR jobs API 或本地后端。
- **听力（`模型与设置 → 听力切分`）**：按需安装 WhisperX 运行时与模型（默认 `small` 多语种）；无需听力的批次可直接跳过。

### 3. 题库连接与分步上传（`模型与设置 → 上传设置`）

支持配置题库 API、科目、凭据及 SSH 隧道。上传严格分步进行：

1. **本地准备与硬校验**：校验题源、结构、原文归属、教材映射与知识点有效性；
2. **人工确认与词条注册**：确认后按幂等键注册缺失答案考点并刷新目录、冻结上传载荷；
3. **写入远端暂存 → 远端草稿 → 发布闸门确认**。

> 手动点上传时，只要本地 `validate` 与结构/标签硬校验通过，审核差异或未完成状态不会阻断上传。

---

## 数据目录与打包

### 数据目录

默认根目录为 `~/.dsh/wf-plugin-data/`（可通过环境变量 `WF_DATA_DIR` 覆盖）：

- `workflows/`：正式批次目录
- `inbox/`：待处理收件箱
- `.workbench-trash/`：可恢复归档目录

### 分发打包

仅打包代码、规则与配置模板，**切勿**包含真实试卷、凭据、音频模型或本机状态：

```bash
zip -r md-upload-tiku.zip \
  lib app/workbench app/workflow app/docs app/references app/scripts app/skills-reference \
  backend config locale \
  app/AGENTS.md app/README.md README.md package.json cordis.patch.yml icon.svg .gitignore
```

---

## 命令行排查与开发测试

### 常用排查命令

```bash
# 环境与契约自检
PYTHONPATH=app python3 -m workflow.doctor

# 查看指定批次的 19 阶段状态
PYTHONPATH=app python3 -m workflow.runner status --batch-dir workflows/<batch-name>

# 查看下一步可执行动作
PYTHONPATH=app python3 -m workflow.agent next --batch-dir workflows/<batch-name>

# 仅生成模型任务包（不调用模型）
PYTHONPATH=app python3 -m workflow.local_agent --stage types --batch-dir workflows/<batch-name> --dry-run

# 自动推进一轮收件箱批次
PYTHONPATH=app python3 -m workflow.auto run --inbox inbox --once

# 卡点台账查询与同步
PYTHONPATH=app python3 -m workflow.issue_log list --status open
PYTHONPATH=app python3 -m workflow.issue_log sync --all

# 六维批次健康统计（区分硬校验、审核完成、质量放行、首次直通、自愈复验与仅流程继续）
PYTHONPATH=app python3 -m workflow.batch_metrics workflows/<batch-name>
```

### 目录结构

```text
src/client/                    前端模块化源码（16 个片段，修改后执行 build-client.mjs）
tools/build-client.mjs         拼接生成 lib/client.js 的构建脚本
lib/client.js                  DSH 原生前端面板（构建产物，勿直接手改）
lib/index.js                   插件宿主、后端生命周期与配置接口
app/workbench/server.py        纯 REST 工作台后端
app/workflow/                  19 阶段引擎、闸门、审核、快照互斥、诊断与健康统计
app/audio/                     WhisperX 听力安装、转写与切段
backend/wf-router.mjs|.sh      跨平台模型路由入口
config/                        模型、审核与提示词模板
app/docs/                      各子系统设计文档与完整硬规则
CHANGELOG/                     行为与规则变更记录（本地忽略）
ISSUES/                        卡点台账（open / fixed）
```

### 运行测试

```bash
# 前端构建与宿主全量语法检查
node tools/build-client.mjs
node --check lib/*.js
node --test backend/*.test.cjs backend/*.test.mjs

# Python 工作流定向测试（仓库根目录）
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app python3 -m unittest \
  workflow.test_stability_hardening \
  workflow.test_operations \
  workflow.test_ai_supplement \
  workflow.test_review_child_source \
  workflow.test_upload
```

---

## 安全与工程边界

- **严禁提交敏感数据**：真实试卷、答案、凭据与本机绝对路径不得入库。
- **禁止伪造状态**：不得手工编辑 `workflow/manifest.json` 跳过校验，不得以「已执行」冒充「审核通过」。
- **尊重用户数据与授权**：不自动删除用户批次，不在未经确认的情况下写入远端题库或发布。
- 修改核心逻辑前请先阅读 [`AGENTS.md`](AGENTS.md)、[`app/AGENTS.md`](app/AGENTS.md) 与 [`app/docs/`](app/docs/)，重要行为变更请同步记录至 [`CHANGELOG/`](CHANGELOG/)。
