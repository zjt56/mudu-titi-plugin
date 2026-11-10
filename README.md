# md-upload-tiku · 录题插件版

英语试卷录题与题库上传插件，运行在 **DeepSeek Harness（DSH）** 中。

它把一份或多份 Word/PDF 试卷（可带答案、解析、听力音频）整理成结构化、可审核、可上传的批次：
提取原文 → 确认结构 → 前置组装题型 → 填充答案/解析/知识点 → 汇总装配 → 本地体检 → 两轮内容审核 → 最终格式检查。
**远端上传与发布始终需要你明确操作**，插件绝不自动提交题库。

- 唯一前端：`lib/client.js` 的原生面板；
- 后端：`app/workbench/server.py` 的纯 REST 服务，不含任何网页；
- 阶段契约唯一来源：[`app/workflow/contracts.py`](app/workflow/contracts.py)（共 18 个阶段）。

---

## 1. 核心设计原则

| 原则 | 含义 |
|---|---|
| **零人工本地执行** | 本地全流程自动跑完，不因“待复核/待确认”停住让人逐题点。 |
| **卡点必须自愈** | 出问题由 AI 诊断、实际修复、复验、续跑；同一输入与代码上重复失败会被拒绝并升级，而不是原样重试空转。 |
| **质量 ≠ 执行** | “已执行、有差异”不等于通过；`review_gate` 不会因此放行。 |
| **差异是产出，不是卡点** | 一审/二审与原标签不一致的条目一律留在人工队列，供你判断模型偏差；不自动改标签、不自动改判通过，也不阻断你手动上传。 |
| **不自动上传/发布** | `upload_gate / upload / publish_gate` 本地一律跳过，只有你手动点上传才提交。 |
| **原文保真** | 题干/选项/材料/表格必须等于原卷切片；Word > PDF/OCR > 空占位。 |

---

## 2. 初始化与依赖

第一次安装后打开侧边栏「录题工作流」→「模型与设置」→「初始化引导」，逐项检查：

| 项目 | 必需性 | 用途 |
|---|---|---|
| Python 运行时 | 必须 | 工作流与本地 REST 后端（建议 3.12，最低 3.9） |
| 默认大模型 | 必须 | 答案、题型、解析、知识点、装配与审核 |
| ffmpeg / ffprobe | 听力时必须 | 音频切片、时长与格式处理 |
| 听力运行时与模型 | 按需 | WhisperX 转写、锚点与切片（点击后才下载） |
| OCR | 按需 | 扫描件、图片型 PDF、浮动图片与版面不确定内容 |
| 题库连接 | 上传时需要 | 读取目录、创建上传暂存 |

- 普通文字 Word/PDF **不需要**先配 OCR；扫描件/图片型试卷再配置。
- 听力模型很大，**只有处理听力时才安装**，不随插件本体分发。
- OCR 与题库密钥只显示“是否已配置”，不会在面板回显密钥。

---

## 3. 安装

### 作为 DSH 插件安装

前提：已安装 DSH；Node.js ≥ 18；Python ≥ 3.9（建议 3.12）；听力需 ffmpeg/ffprobe。

```bash
git clone <repository-url> md-upload-tiku
cd md-upload-tiku
dsh plugin --profile <profile-name> add "$(pwd)"
```

也可用 DSH 插件管理器安装本目录绝对路径。安装后重启 Harness，侧边栏出现「录题工作流」。

### 给其他用户打包

只带代码和规则，**不要**带真实试卷、运行时模型、凭据、验证目录或本机配置：

```bash
zip -r md-upload-tiku.zip \
  lib app/workbench app/workflow app/docs app/references app/scripts app/skills-reference \
  backend config locale \
  app/AGENTS.md app/README.md README.md package.json cordis.patch.yml icon.svg .gitignore
```

---

## 4. 支持的输入

- `.docx`：主格式，Word 原生文字与格式证据优先；
- `.pdf`：优先文字层，必要时按页 OCR；
- `.mp3 / .wav / .m4a / .ogg / .flac`：可选听力音频；
- 不支持旧版 `.doc`，请先另存为 `.docx`（避免二进制解析丢图丢格式）。

Word 与 PDF 同时存在时以 **Word 原文为准**；PDF/OCR 只辅助版面、层级与缺失兜底，不覆盖已有 Word 内容。
答案/解析/文章大意的来源优先级同样是 **Word > PDF/OCR > 空占位**，且禁止生成、改写或跨材料借用。

---

## 5. 工作流（18 阶段）

```text
extract        原卷复合提取（Word 原生 / PDF 文字层 / 必要 OCR）
structure_gate 结构自动确认（题号覆盖、题组边界、去噪、答案分离）
preassemble    前置批次组装（含目录题型/子类/词数判定）
answers        答案填充         ┐
audio          听力切段/转写     ├ 三者并行（听力不阻塞组装）
types          题型交接校验     ┘
explanations   解析与文章大意（等待答案）
knowledge      知识点与教材匹配
assemble       汇总装配与固化（唯一确定性合并点）
meta           难度与分类
underlines     画线标记
validate       本地自动体检（硬校验）
review         两轮内容审核（一审全量 + 二审抽检）
review_gate    审核放行判断（差异待人工 = needs_review，不阻断手动上传）
format_gate    最终上传格式检查
upload_gate    上传人工确认
upload         写入远端上传暂存
publish_gate   发布人工确认
```

要点：

- 题型在 `preassemble` 内完成，不再单独调用一轮题型模型；
- 听力（`audio`）最耗时，只阻塞内容审核（`review` 需读听力转写核对语义），不阻塞答案/知识点/装配/体检；
- 没有答案、解析或文章大意时保留「无」占位，不编造；
- **书面表达**不按客观题严格校验：无固定答案、范文可有可无，缺失填「无」正常，有范文只提示不阻断；
- OCR/图片/音频能力受限只作提示或未核验信息，不伪装成语义通过；
- 结构、字段、原文保真、格式仍是硬校验。

---

## 6. 审核与差异（重点）

- 人工审核只收两类差异：**知识点标签差异**与**基本差异**（题型/子类/答案/解析/原文）；难度/来源/分类/词数不进审核。
- 一审/二审与原标签不一致就是差异，必须完整进 `07_review/manual-review-queue.json` 与 `review-gate.json` 的 `rounds.errors`。
- `review_gate = needs_review + execution_completed + continue_unqualified + release_ready=false` 是**设计状态**，显示为“差异待人工判断”（`failure_class=quality_differences`），**不算卡点、不触发改标签/局部重生成、不阻断你手动上传**。
- 只有 `blocked` 的确定性阻断项（结构冲突、标签体检、审核协议失败等）才是真正的卡点。

---

## 7. 状态与可观测性

- 流程图 ≤ 10 节点，与真实阶段同步，能区分听力/知识点等分项。
- 实际长任务（结构确认、体检、审核闸门、卷头识别）通过独立的执行生命周期记录真实“处理中”，不会把“依赖已满足但仍在等待”伪装成运行。
- 手动“继续处理”会清掉历史自愈冷却/重试预算，不会被旧的 `retry_budget_exhausted` 卡住。
- 进度轮询有顺序保护：旧响应不会覆盖新进度；窗口重新获得焦点立即刷新。

---

## 8. AI 运维辅助（附加能力）

- **只在有活动批次/失败事件时巡检**，无任务不消耗运维模型调用。
- 接入顺序：**先诊断（后端确定性证据包）→ 再判断方向**（观察 / 重跑阶段 / 局部重生成 / 隔离代码修复 / 交人工），不直接“接管整卷”。
- 只在批次身份、尝试和写锁有效时执行受控动作；未知请求先查回执，**禁止盲重发**。
- 只有后端复验证据成立才显示“已恢复”；模型自述、会话空闲、安排重试都不算完成。
- 内容错误只按受影响题号局部重生成（限答案/解析/题型/知识点/听力），未定位到题号就拒绝，不整卷重跑、不改上传边界。
- 审核差异、限流等不触发运维修复。
- 状态明确区分：待命 / 正在接手 / 调查 / 修复 / 复验 / 冷却等待 / 接手过期 / 待核对动作回执 / 模型配置受阻。
- 关闭该能力后，原有录题与自愈继续运行。详见 [Harness 运维说明](app/docs/harness-operations.md)。

---

## 9. 模型配置

在「模型与设置 → 大模型配置」选择默认模型，也可分别指定阶段与两轮审核模型。

- 路由保存在本机 `~/.dsh/plugin-state/md-upload-tiku/model-router.json`；仓库 `config/model-router.json` 只是默认模板，`default` 为空。
- **仓库不固定任何模型**：`default` 留空时，每台设备自动跟随自己 Harness 菜单当前选中的模型。
- 一旦显式配置 provider/model，就严格使用，配置损坏直接报错，**绝不静默切换到其它模型**。
- 升级或重新检出仓库不会覆盖你选好的模型。
- 凭据由 Harness 凭据系统提供，不写入题目文件、任务包或浏览器存储。

---

## 10. OCR

在「模型与设置 → OCR 识别」配置，三态策略：

- `auto`（默认）：只在图片型内容、文字层不足、浮动对象、符号字体或版面不确定时调用；
- `on`：强制启用 OCR 辅助；
- `off`：关闭，只用原生文字层（图片型试卷将明确报错，不伪造完成）。

OCR 走 PaddleOCR jobs API 或已配置本地后端；Token 存于项目目录之外的私有状态目录。

---

## 11. 听力

在「模型与设置 → 听力切分」安装 WhisperX 运行时与模型，再对有音频批次切段。默认 small 多语种模型；模型路径、设备、线程、对齐模型、HF 镜像等由音频配置管理。

某批次明确不需要听力时可在音频设置中跳过；无切片产物不能伪造音频映射，但已有可用切片不因边界证据不足而阻塞后续独立流程。

---

## 12. 题库连接与上传

在「模型与设置 → 上传设置」配置题库 API、科目、账号与密钥；支持 SSH 隧道访问远端。连接测试只读目录，不代表已获得上传权限。

上传分步：

1. 本地准备并冻结当前批次；
2. 你确认目标、批次与预检结果；
3. 写入远端上传暂存；
4. 远端人工确认后生成草稿；
5. 你在发布闸门明确确认后发布。

**手动点上传时，批次过期、内容变化、审核未完成或存在审核差异都不阻碍上传**；只要本地 `validate` 曾通过且标签/结构硬校验通过即可。凭据、私钥、API Key 不得写入仓库或任务包。

---

## 13. 数据位置

默认数据根目录 `~/.dsh/wf-plugin-data/`，包括：

- `workflows/`：正式批次；
- `inbox/`：上传暂存；
- `.workbench-trash/`：可恢复归档；
- 音频运行时、模型与状态目录。

插件目录内若已存在旧版 `workflows/` 会优先兼容。可用 `WF_DATA_DIR` 指定数据根目录。真实批次与运行时目录均不应提交 Git。

---

## 14. 命令行排查

从仓库根目录执行；项目内 Python 环境先设 `PYTHONPATH=app`：

```bash
# 环境与契约自检
PYTHONPATH=app python3 -m workflow.doctor

# 查看批次阶段
PYTHONPATH=app python3 -m workflow.runner status --batch-dir workflows/<batch-name>

# 查看下一步动作
PYTHONPATH=app python3 -m workflow.agent next --batch-dir workflows/<batch-name>

# 只生成模型任务包，不调用模型
PYTHONPATH=app python3 -m workflow.local_agent --stage types --batch-dir workflows/<batch-name> --dry-run

# 自动推进一轮
PYTHONPATH=app python3 -m workflow.auto run --inbox inbox --once

# 卡点台账：查看/登记/修复
PYTHONPATH=app python3 -m workflow.issue_log list --status open
PYTHONPATH=app python3 -m workflow.issue_log sync --all
```

无自动模型配置时，`workflow.agent next` 会导出任务包（`README.md`、`task.json`、`response.schema.json`、示例结果）。只有导入返回 `completed=true` 才代表阶段真正通过程序校验。

---

## 15. 开发与测试

```text
lib/client.js                  DSH 原生前端面板
lib/index.js                   插件宿主、后端生命周期与 REST 路由
app/workbench/server.py        纯 REST 工作台后端（不提供网页）
app/workflow/                  阶段、闸门、交接、审核与导入协议
app/audio/                     听力安装、转写与切片
backend/wf-router.sh|.mjs      模型路由入口
config/                        模型、审核与提示词配置模板
app/docs/                      工作流专项文档
CHANGELOG/                     变更与规则记录
ISSUES/                        卡点台账（open/fixed）
```

常用验证：

```bash
# Python 工作流测试（从 app/ 目录）
cd app
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest workflow.test_agent_protocol

# 后端与插件接口测试
cd ..
node --test backend/*.test.cjs

# 宿主插件语法检查
node --check lib/index.js lib/client.js
```

修改行为前先读 [`AGENTS.md`](AGENTS.md)、[`app/AGENTS.md`](app/AGENTS.md) 与对应 `app/docs/`；所有重要行为变化必须追加 `CHANGELOG/` 记录，卡点须登记到 `ISSUES/`。

---

## 16. 安全边界

- 不把真实试卷、答案、Token、API Key、密码、私钥、本机路径提交到仓库；
- 不直接编辑批次 `workflow/manifest.json` 伪造完成状态；
- 不自动删除用户试卷或归档批次；
- 不把 OCR/图片/音频能力限制误报成内容正确；
- 不以“已执行”代替“审核通过”；
- 不在未获得你确认时上传或发布。
