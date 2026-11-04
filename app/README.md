# 录题工作流（workflow + workbench）

把一份英语试卷（docx / pdf，可带答案卷与听力音频）整理成题库可以接收的结构化数据，并在本地网页上看题、改标签、处理人工意见。

本仓库只放**流水线与前后端代码**，不含技能说明（`SKILL.md`、`references/*.md`）与任何真实试卷数据；关键的执行标准另见 `docs/`。

录题技能源文的固定提交摘要见 [`skills-reference/`](skills-reference/README.md)，其中记录在线来源、历史冲突与本次明确决定；它不是原文全集，也不自动覆盖用户要求或代码事实。标签的题组/子题规则与计划中的共用校验集成见 [知识点与标签口径](docs/knowledge-tagging.md)。

## 组成

```
workflow/           流水线：阶段契约、清单/台账、检查、审核、自动推进
  runner.py         单步执行（解析、答案、听力切段、题型、知识点、装配、审核……）
  auto.py           批量推进：扫描 inbox、初始化批次、按台账推进、遇闸门停住
  contracts.py      16 个阶段的契约（输入、产出、责任、文档出处、闸门）
  manifest.py       批次台账 workflow/manifest.json，阶段状态与终态
  report.py         产物自检与汇总
  review.py         审核规则（内容/标签/结构）
  structure_checks.py  结构六项 + 内容范围（逐题边界、表格、图片）
  structure_gate.py    结构自动闸门：去噪+元信息抽取+按题型结构化+自检
  denoise.py        正文去噪与卷名/年份/时长等元信息抽取
  question_content.py  逐题内容边界契约（独立题/题组/排除项）
  independent_review.py 独立复核（快照、签名、三层覆盖、20% 抽查）
  preview.py        原卷切片：按题号/题区间取正文、表格、图片
  ingest.py inventory.py checks.py doctor.py
  pipelines/        工作流脚本（Node 模拟可跑）
  templates/preview.html
workbench/          本地审核台（Python 标准库 HTTP 服务 + Vue 3 + Element Plus）
  server.py         REST API：批次、单元、标签、处理意见、图片体检、上传、流程推进
  taxonomy.py       27 个固定题型、子类、难度、分类、知识点目录（只认叶子）
  media_audit.py    图片体检：题目图 / 卷头 / 栏目图 / 未引用 / 已排除
  static/           前端（本地 vendor，无构建步骤）
references/         规则数据：题型框架、知识点目录、难度分类、题型-知识点映射
```

## Agent 接入与执行器

接手本仓库的 Agent 请先读 [AGENTS.md](AGENTS.md)。无需指定厂商的 CLI：运行 `python -m workflow.agent next --batch-dir workflows/<批次>`，按返回的任务包读取任务、交回结果，程序负责校验并登记阶段。

- [通用 Agent 接口 v1](docs/agent-interface.md)：文件任务包、标准结果协议、任意 CLI 适配、迁移与交接说明。
- [上传暂存 API 对接](docs/upload-api.md)：读取当前远端目录、媒体核验、冻结最终请求、人工批准后提交与暂存回执。
- [复合原卷提取](docs/extraction.md)：Word 原生读取，PDF 逐页路由到文字层或本地 OCR，保留结构候选及证据。
- [可选本机 CLI 执行](docs/local-agent.md)：通用命令、DSH / Codex 适配器及调用日志和平台限制。

## 跑起来

需要 Python 3.9+（开发用 3.12），无第三方依赖；前端库已放 `workbench/static/vendor/`。

```bash
# 起审核台（默认 8765）
python -m workbench.server --port 8765

# 自检：环境、文件、规则、批次
python -m workflow.doctor

# 跑一遍自动化（扫描 inbox，推进已有批次，遇闸门停住）
python -m workflow.auto run --inbox inbox --once
```

打开 <http://127.0.0.1:8765>：

- **首页**：上传原卷（docx/pdf，可附答案卷与听力音频），提交后自动建批次、推进流程，跳到该批次；
- **试卷审核**：左栏是「需要审核的试卷」，一行一份；中栏按入库单元列题目（题组一行，如 `31–40 10小题`）；右侧左边原卷预览、右边待人工处理。标签按单元改（组题整体一套），处理意见可编辑或「手动修改」。
- 键盘 `↑`/`↓` 或 `k`/`j` 在题目列表里切换。

## 执行标准（docs/）

代码之外，必须遵守的口径都写在 `docs/`，开工前至少读 `docs/README.md` 的速览表：

| 文档 | 管什么 |
| --- | --- |
| [docs/README.md](docs/README.md) | 文档索引 + 必须记住的硬数字与规则 |
| [docs/quickstart.md](docs/quickstart.md) | 快速安装与启动：网页端、智能体、本机 Agent、上传 API |
| [docs/pipeline.md](docs/pipeline.md) | 16 个阶段、批次目录、闸门、自动化推进与禁止事项 |
| [docs/constraints.md](docs/constraints.md) | 硬约束 A–G 的适用子集（题型映射、单元粒度、媒体、内容真实性） |
| [docs/structure.md](docs/structure.md) | 逐题内容边界、六项结构检查 + 内容范围 |
| [docs/knowledge-tagging.md](docs/knowledge-tagging.md) | 知识点只写叶子、题组/子题分层维度、来源裁决与校验集成 |
| [docs/review.md](docs/review.md) | 三审与 20% 抽样、人工队列口径、独立复核的边界 |
| [docs/media.md](docs/media.md) | 图片体检、听力切段、卷面预览样式 |

## 数据落在哪里

```
inbox/<试卷名>/            上传的原卷、答案卷、音频、batch.json
workflows/<试卷名>/
  01_source/ … 06_batch/   各阶段产物（解析、听力、结构、知识点、装配）
  07_review/               审核结果、人工队列、校验报告
  candidates/              审核台的草稿（含 units/<unit_id>.json 的单元标签）
  workflow/manifest.json   阶段台账；run-log.jsonl 执行日志；decisions.jsonl 人工决定
```

覆盖写之前一律留 `.bak`；审核台只写草稿与队列，**不上传、不发布**，上传与发布闸门只能人工确认。

## 流程闸门

16 个阶段里，代理阶段（解析、答案、听力、题型、解析与大意、知识点、装配、审核）需要 `workflow/agent-bridge.json` 指定外部代理命令（模板见 `workflow/agent-bridge.example.json`）；没配桥时流程会停在「等待代理执行」，不会假装跑过。结构确认已改为自动机器闸门（去噪+元信息抽取+按题型结构化+自检，见 `workflow/structure_gate.py`）；审核放行是机器闸门；上传确认、发布确认仍是人工闸门，代码里没有自动通过的口子。

## 测试

```bash
python -m unittest discover -s workflow -p 'test_*.py'
```

需要真实样例批次的回归用 `skipUnless` 保护：本地把样例放在 `workflows/_real_2025/` 才会跑，仓库里不带试卷数据。

## 许可与内容

仓库不含任何试卷原文；`workflows/`、`inbox/` 默认被 `.gitignore` 排除。上传与发布接口未包含在此仓库（涉及题库写入与凭据），这里只做到草稿与人工队列。
