# md-upload-tiku

英语试卷录题与题库上传插件，运行在 DeepSeek Harness（DSH）中。

它把一份或多份 Word/PDF 试卷、答案、解析和可选听力音频整理成结构化批次：提取原文，确认题目边界，填充答案/题型/解析/知识点，组装上传格式，执行本地体检和差异审核。远端上传和发布始终需要用户明确操作。

## 先看这里：初始化引导

插件安装后，打开 DSH 侧边栏的「录题工作流」→「模型与设置」。默认进入「初始化引导」，会检查：

| 项目 | 必需性 | 用途 |
|---|---|---|
| Python 运行时 | 必须 | 工作流和本地 REST 后端 |
| ffmpeg / ffprobe | 听力时必须 | 音频切片、时长和格式处理 |
| 听力运行时与模型 | 按需 | WhisperX 转写、锚点和切片 |
| OCR Token | 按需 | 扫描件、图片型 PDF、浮动图片和版面不确定内容 |
| 默认大模型 | 必须 | 答案、题型、解析、知识点、装配和审核 |
| 题库连接 | 上传时需要 | 读取目录和创建上传暂存 |

听力运行时和模型不随插件本体分发。用户点击「安装运行时与模型」后才下载；已经安装时按钮变为「重新安装 / 更新模型」。OCR 和题库密钥只显示是否已配置，不会在面板中回显密钥。

## 安装

### 使用 DSH 插件

前提：

- 已安装 DeepSeek Harness（DSH）；
- Node.js 18 或更高版本；
- Python 3.9 或更高版本，建议 Python 3.12；
- 需要听力切片时，准备 ffmpeg/ffprobe；插件优先使用随插件提供或已配置的本地二进制。

从仓库安装开发目录：

```bash
git clone <repository-url> md-upload-tiku
cd md-upload-tiku
dsh plugin --profile <profile-name> add "$(pwd)"
```

也可以使用 DSH 的插件管理器，安装本目录的绝对路径。安装后重启 Harness，侧边栏会出现「录题工作流」。

首次使用按「初始化引导」逐项检查。普通文字 Word/PDF 不需要先配置 OCR；扫描件和图片型试卷再配置 OCR。听力模型很大，只有处理听力时才安装。

### 给其他用户打包

只打包代码和规则，不要带真实试卷、运行时模型、凭据、验证目录或本机配置：

```bash
zip -r md-upload-tiku.zip \
  lib app/workbench app/workflow app/docs app/references app/scripts app/skills-reference \
  backend config locale \
  app/AGENTS.md app/README.md README.md package.json cordis.patch.yml icon.svg .gitignore
```

用户拿到压缩包后，用 DSH 插件管理器安装，并重启 Harness。

## 支持的输入

- `.docx`：主格式，Word 原生文字和格式证据优先；
- `.pdf`：使用文字层，必要时按页调用 OCR；
- `.mp3`、`.wav`、`.m4a`、`.ogg`、`.flac`：可选听力音频；
- 不支持旧版 `.doc`。请在 Word/WPS 中另存为 `.docx`，避免二进制文档解析丢失图片和格式。

Word 与 PDF 同时存在时，以 Word 原文为权威；PDF OCR 主要用于版面、层级和辅助证据，不替换 Word 文本。

## 工作流

阶段契约唯一来源是 [`app/workflow/contracts.py`](app/workflow/contracts.py)。主要顺序如下：

```text
提取
  -> 结构确认
  -> 前置批次组装
  -> 答案 / 题型 / 听力 / 解析 / 知识点
  -> 汇总装配
  -> 难度与分类
  -> 画线标记
  -> 本地体检
  -> 两轮审核
  -> 审核放行判断
  -> 最终格式检查
  -> 用户确认上传
  -> 上传暂存
  -> 用户确认发布
```

说明：

- 题型、解析、知识点默认按整张试卷一次分析，避免一题一片重复携带整卷上下文；装配和审核仍按实际输入需要分片。
- 没有答案、解析或文章大意时，保留对应的「无」占位，不编造内容。
- 听力边界证据不足只作为能力提示；只要切片产物能生成并通过结构校验，就不要求模型或人工重新判断边界。
- OCR、图片和音频能力受限不会伪装成语义审核通过；它们按当前审核规则记录为提示或未核验信息。
- 结构、字段、原文保真和格式检查仍是硬校验。
- `upload_gate`、`upload`、`publish_gate` 不会自动提交或发布。
- 用户手动点击上传时，批次过期、内容变化、审核未完成或存在审核差异不作为上传阻断；本地 `validate` 曾通过且标签/结构硬校验通过即可继续。

## AI 运维辅助

排队区选中批次后，可在「AI 运维」查看异常接手、调查、修复、复验和续跑记录。原有自愈实际失败后，插件会使用 Harness 专用持久会话进一步调查，正常批次不持续调用运维模型。该能力有独立开关，关闭后原有录题与自愈继续运行。

运维会话只能通过批次范围内的工具读取证据、预演与应用已有受控修复、复验、交回正常队列续跑。只有后端验证证据成立才显示已恢复；模型自述、已安排重试和会话空闲不算完成。遇到未知程序缺陷会记录「需深入修复」及复现证据，不直接修改正在运行的插件或自动上传题库。

模型来自 `config/model-router.json` 的可选 `roles.operations`（可在「模型与设置 → 大模型配置 → AI运维」单独选择），未单列时跟随本机 Harness 当前模型，再没有时使用 `default`；显式角色配置非法时不回落到其它模型。当前宿主缺少原生会话服务时会显示不可用，原流程保留。详见 [Harness 运维说明](app/docs/harness-operations.md)。

## 模型配置

在「模型与设置 → 大模型配置」选择默认模型，也可以分别指定阶段和审核轮次模型。路由写入 `config/model-router.json`，由插件生成生效配置后，经 `backend/wf-router.sh` / `backend/wf-router.mjs` 按阶段调用。

**仓库不固定任何模型**：`default` 留空时，每台设备自动跟随自己 Harness 菜单里当前选中的模型（读取该设备 `profiles/<profile>/cordis.patch.yml` 的 `agent-default-model`）。也可以在插件设置里显式指定 default 或按阶段/角色覆盖；一旦显式配置，选中的 provider/model 一定被严格使用，配置损坏时直接报错，不会静默切换到其他模型。凭据由 Harness 的凭据系统提供，不写入题目文件、任务包或浏览器存储。

插件生效配置中的本地并发默认较保守：分片并发和审核并发为 4，用于保护本机内存；这不是 API 限流。标注阶段默认整卷一次分析，因此通常不会启动大量分片进程。

## OCR

在「模型与设置 → OCR 识别」配置。OCR 是三态策略：

- `auto`：默认，只在图片型内容、文字层不足、浮动对象、符号字体或版面不确定时调用；
- `on`：强制启用 OCR 辅助；
- `off`：关闭 OCR 辅助，只使用原生文字层。

OCR 使用 PaddleOCR jobs API 或已配置的本地 OCR 后端。Token 保存在项目目录之外的私有状态目录，不写入仓库和题目 JSON。完整测试会实际提交并轮询任务，可能消耗 OCR 服务额度。

## 听力

在「模型与设置 → 听力切分」中安装 WhisperX 运行时和模型，然后选择有音频的批次执行切段。音频状态和下载进度在该页面查看。

听力安装会按当前配置创建本地运行环境并下载模型，不改变插件本体大小。默认使用 small 多语种模型；模型路径、设备、CPU 线程、对齐模型和 Hugging Face 镜像等由音频配置管理。

如果某批次明确不需要听力，可在音频设置中跳过听力。没有切片产物时不能伪造音频映射；但已有可用切片不因为边界证据不足而阻塞后续独立流程。

## 题库连接与上传

在「模型与设置 → 上传设置」配置题库 API、科目、账号和密钥。默认 API 地址为本机回环地址，插件支持通过 SSH 隧道访问远端题库。连接测试只读取目录，不提交题目，也不代表已经获得上传权限。

上传过程分为：

1. 本地准备上传和冻结当前批次；
2. 用户确认目标、批次和预检结果；
3. 写入远端上传暂存；
4. 远端人工确认后生成草稿；
5. 用户在发布闸门明确确认后发布。

插件不会自动上传、确认或发布。凭据、私钥和 API Key 不应写入仓库或任务包。

## 数据位置

默认数据根目录为 `$DSH_HOME/wf-plugin-data/`，通常是 `~/.dsh/wf-plugin-data/`，包括：

- `workflows/`：正式批次；
- `inbox/`：上传暂存；
- `.workbench-trash/`：可恢复归档；
- 音频运行时、模型和状态目录。

如果插件目录中已经存在旧版 `workflows/`，会优先兼容原位置。可以用 `WF_DATA_DIR` 指定数据根目录。真实批次和运行时目录均不应提交 Git。

## 命令行排查

所有命令从仓库根目录执行。若使用项目内 Python 环境，先设置 `PYTHONPATH=app`：

```bash
# 环境和契约自检
PYTHONPATH=app python3 -m workflow.doctor

# 查看批次阶段
PYTHONPATH=app python3 -m workflow.runner status \
  --batch-dir workflows/<batch-name>

# 查看下一步动作
PYTHONPATH=app python3 -m workflow.agent next \
  --batch-dir workflows/<batch-name>

# 只生成模型任务包，不调用模型
PYTHONPATH=app python3 -m workflow.local_agent \
  --stage types --batch-dir workflows/<batch-name> --dry-run

# 自动推进一轮
PYTHONPATH=app python3 -m workflow.auto run --inbox inbox --once
```

没有自动模型配置时，`workflow.agent next` 会导出任务包，任务包包含 `README.md`、`task.json`、`response.schema.json` 和示例结果。只有导入结果返回 `completed=true` 才代表阶段真正通过程序校验。

## 开发与测试

代码结构：

```text
lib/client.js                    DSH 原生前端面板
lib/index.js                     插件宿主、后端生命周期和 REST 路由
app/workbench/server.py          纯 REST 工作台后端，不提供网页
app/workflow/                    阶段、闸门、交接、审核和导入协议
app/audio/                       听力安装、转写和切片
backend/wf-router.sh             模型路由入口
config/                          模型、审核和提示词配置
app/docs/                        工作流专项文档
CHANGELOG/                       变更与规则记录
```

常用验证命令：

```bash
# Python 工作流相关测试
cd app
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest workflow.test_agent_protocol

# 后端和插件接口测试
cd ..
node --test backend/*.test.cjs backend/*.test.mjs

# 宿主插件语法检查
node --check lib/index.js lib/client.js
```

修改行为前先阅读 [`AGENTS.md`](AGENTS.md)、[`app/AGENTS.md`](app/AGENTS.md) 和对应的 `app/docs/` 文档。所有重要行为变化都要追加 `CHANGELOG/` 记录。

## 安全边界

- 不把真实试卷、答案、Token、API Key、密码、私钥和本机路径提交到仓库；
- 不直接编辑批次 `workflow/manifest.json` 伪造完成状态；
- 不自动删除用户试卷或归档批次；
- 不把 OCR、图片或音频能力限制误报成内容正确；
- 不以“已执行”代替审核通过；
- 不在未获得用户确认时上传或发布。
