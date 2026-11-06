# 录题流水线文档

这篇是 `docs/` 的文档入口。接手工作流的 Agent 先读根目录 [AGENTS.md](../AGENTS.md) 和 [通用 Agent 接口](agent-interface.md)，再按具体阶段查规则。

仓库只有流水线代码（`workflow/`）和纯 REST API 后端（`workbench/`），不含真实试卷、历史批次与任何前端页面；界面在项目根的 `lib/client.js`。
文档里的数字全部按代码核对过；凡与本篇冲突，以代码为准。

## 文档索引

| 文档 | 管什么 | 先看它的人 |
| --- | --- | --- |
| [quickstart.md](quickstart.md) | 快速安装与启动：DSH 插件、智能体、本机 Agent、上传 API | 第一次部署或把项目交给别人使用的人 |
| [agent-interface.md](agent-interface.md) | 通用任务包、结果协议、领取/交回、任意 CLI 适配 | 换电脑、交给其他人或其他 Agent 的使用者 |
| [local-agent.md](local-agent.md) | 可选本机 CLI 配置、日志与平台限制 | 需要自动调用本机 Agent 的使用者 |
| [extraction.md](extraction.md) | 原生 Word、PDF 按页路由、本地 OCR 与证据 | 配置原卷提取的使用者 |
| [workflow-regressions.md](workflow-regressions.md) | 本轮切题、来源、目录、审核问题及强制回归约束 | 所有执行者 |
| [upload-api.md](upload-api.md) | 安全连接、当前目录、API 字段映射、冻结/批准/暂存、防重复 | 对接远端上传的使用者 |
| [layout-review.md](layout-review.md) | 整页版面 OCR 提交、续查、失败处理及证据边界 | 复核复杂版面的人 |
| [pipeline.md](pipeline.md) | 16 个阶段、批次目录、自动驱动、代理桥、禁止事项 | 要把一批卷子从头跑到尾的人 |
| [constraints.md](constraints.md) | 会直接导致返工或入库失败的硬约束（A–G 子集） | 正在录题、写 `batch.json` 的人 |
| [structure.md](structure.md) | 逐题内容边界、题组、表格、七项结构判定 | 切题、建单元、做结构确认的人 |
| [knowledge-tagging.md](knowledge-tagging.md) | 知识点只写叶子、按题型的维度表、组题标签 | 打标签、审标签的人 |
| [content-labeling.md](content-labeling.md) | 打标签四个子智能体：解析与大意、知识点与教材、题型与词数、答案与难度 |
| [textbook-catalog.md](textbook-catalog.md) | 课时同步练习用教材目录树（三版本 18 册 128 单元） |
| [review.md](review.md) | 四路打标（解析/知识点/题型/答案）→ AI 审核 → 一审 → 二审抽样、人工队列、独立复核 | 做审核、处理队列的人 |
| [media.md](media.md) | 图片 verdict、媒体排除清单、听力切分、PDF 草稿、卷面预览契约 | 处理图片、音频、表格与预览的人 |

## 十分钟速览

只记这张表，其余细节进各篇查。

| 项 | 值 | 说明 |
| --- | --- | --- |
| 阶段数 | **15** | `extract` 起、`publish_gate` 止，契约固定 |
| 阶段类型 | **4** | `agent` / `command` / `gate` / `human` |
| 题型主类 | **27** | 固定，不新增、不改名、不合并 |
| 知识点目录 | 版本 **1.1**，**522** 个节点 / **408** 个叶子 | 只读目录，能落库的只有叶子 |
| 结构核对 | **6 项 + 1 项** | 六项结构检查，外加 `content_scope` 逐题内容范围 |
| 结构项数 | **7** | `structure_checks.CHECKS` 的键数 |
| 知识点写法 | **只写叶子** | 写到中间层＝`知识点必须是叶子` |
| 组题标签 | **单元级一份** | 子题不重复存同一套标签 |
| 单题标签数 | **2–4 个** | 同维度最多一个 |
| 二审抽样 | **20%，向上取整** | 按小题抽 `ceil(小题数 × 20%)`；整组题型按**整组**抽，只做组级核对 |
| 审核终态 | **`fixed` / `rejected`** | 审核台口径；`review_gate` 把 `accepted` 也算已有决定，见 [review.md](review.md#状态口径) |
| 本地体检项 | **18 个脚本配置项 + 源头对账** | 缺输入显式说明；源头逐题对账始终阻断缺题与跨材料 |
| 问题级别 | **3 级** | `blocker` > `major` > `minor` |
| 上传、发布 | **永远人工** | 任何自动流程都不得代劳 |

### 十条必须记住的规则

1. **16 个阶段**按契约顺序跑，前一个不是终态就不能开下一个；`completed` / `approved` / `skipped` 才算完成。
2. **27 个主类**是封闭集合；子类只能取该主类目录里列出的值，无子类题型必须留空。
3. **七项结构核对**：`content_complete`、`stem_matches`、`questions_options_complete`、`alignment`、`images`、`grouping`，加第 7 项 `content_scope`。
4. **知识点只写叶子**，并且**名称与 id 必须成对**；只有名字没有 id，题库按知识点筛不到。
5. **组题标签挂在单元上**（`applies_to=unit`、`inherit_subquestions=true`）；子题只留自己的设题角度，不挂体裁。
6. **一题 2–4 个标签、同维度最多一个**；组级写全；整组题型子题 `knowledge=[]`，独立单题才各写自身标签，不靠堆标签解决多角度。
7. **二审按 20% 抽样并向上取整**：整组题型（完形/阅读/任务型阅读/补全对话/信息转换/选词·短文·教材语篇填空/听力短文·长对话理解）整组抽取、只做组级核对，不拆小题；其余题型按小题抽。抽不中不代表通过，抽样只证明抽中的那些被核对过。
8. **审核台里只有 `fixed` / `rejected` 算处理完**，`accepted` 仍留在待处理计数；`review_gate` 另有一套口径（`accepted` 视为已有决定），两者差异见 [review.md](review.md#状态口径)。
9. **`unchecked` 不是 `failed`**：缺证据是"还没核对"，不是"已判定错误"，补证或转人工。
10. **上传与发布永远人工**：`upload_gate`、`publish_gate` 不允许被自动标记通过。

## 一分钟跑起来

```powershell
cd E:\录题工作流\upload-workflow
python -m workflow.doctor                       # 环境/文件/规则/契约/闸门/批次自检
python -m workflow.auto scan --inbox inbox      # 看收件箱能识别出哪些批次
python -m workflow.auto run  --inbox inbox --once
python3.12 -m workbench.server --port 8791      # REST API 后端（由插件拉起）
```

跑完一轮先看 `workflows/_queue.json`：每条批次停在哪个阶段、为什么停，`action` 字段直接告诉你是等人、等代理、等闸门，还是脚本失败。详见 [pipeline.md](pipeline.md)。

## 一批卷子的一生

```text
原件/答案/音频
   │  extract（先识别来源与元信息）
   ▼
结构与题号候选 ──→ structure_gate（源题号/选项/材料/对话分组独立复核）
   │  answers / audio / types / knowledge
   ▼
题型、答案、知识点
   │  assemble / meta / underlines
   ▼
batch.json ──→ batch_meta.json
   │  validate（脚本体检 + 源头逐题对账）
   ▼
审核（四路打标 → AI → 一审 → 二审抽样）
   │  review_gate
   ▼
upload_gate（人工）──→ upload（仓库内阻断）──→ publish_gate（人工）
```

两处必须停下来等人的位置：上传确认、发布确认。结构确认已改为自动闸门（去噪+元信息抽取+按题型结构化+机器自检），机器自检不通过时仍会阻断并列出阻断码等人工处理。其余阶段可以由执行者或脚本推进，但推不动时必须写清"停在哪个阶段、为什么"，不能静默跳过。

## 阅读顺序建议

- 第一次接触：本篇 → [pipeline.md](pipeline.md) → [structure.md](structure.md)。
- 要动手录题： [constraints.md](constraints.md) → [structure.md](structure.md) → [knowledge-tagging.md](knowledge-tagging.md)。
- 要做审核： [review.md](review.md)，再回看 [knowledge-tagging.md](knowledge-tagging.md) 的报错文案。
- 要处理图、音频、表格、预览： [media.md](media.md)。

## 术语表

| 术语 | 含义 |
| --- | --- |
| 批次（batch） | 一份原卷对应的一次录入，一个 `workflows/<批次>/` 目录 |
| 单元（unit） | 入库的最小对象：一道独立题，或一个题组 |
| `unit_kind` | 单元类型（独立题 / 题组），上传时定死，事后改不了 |
| 题组（group） | 一段共享材料下的多道小题，标签挂在单元级 |
| 小题（subquestion） | 题组里的成员，二审抽样按它计数 |
| 闸门（gate） | 只能由检查结果放行的阶段，不能手工标记通过 |
| 发现（finding） | 一条待处理的问题，有编号、级别、状态、证据 |
| `unchecked` | 尚未核对（缺证据），不是已判定错误 |
| `stale` | 输入或产物变了，原结论失效，必须重跑 |
| 代理桥 | `workflow/agent-bridge.json`，给 `agent` 阶段提供命令 |

## 常见误解

| 说法 | 实际情况 |
| --- | --- |
| "体检过了就能上传" | 体检只是第 10 阶段，后面还有审核、审核闸门、人工确认 |
| "审核通过就等于发布" | 审核通过只到"可上传"；上传与发布是两道人工闸门 |
| "抽了 20% 就代表整卷没问题" | 抽样只覆盖抽中的小题，覆盖率要单独核对 |
| "`accepted` 就是处理完了" | 只有 `fixed` / `rejected` 算处理完 |
| "`unchecked` 就是错的" | 是"还没人看过"，补证或转人工 |
| "状态可以手工改" | `gate` 类阶段拒绝手工通过，`human` 类只能由人标记 |
| "标签越多越准" | 一题 2–4 个，同维度最多一个 |
| "子题也要挂体裁" | 体裁是材料属性，只挂单元级 |
| "PDF 提出来就能用" | PDF 是草稿素材，题型、答案、知识点都要人工核对 |

## 目录约定

| 目录 | 放什么 | 会不会被提交 |
| --- | --- | --- |
| `workflow/` | 流水线代码、契约、测试 | 是 |
| `workbench/` | 纯 REST API 后端（无页面、无静态文件） | 是 |
| `docs/` | 本目录，执行标准与说明 | 是 |
| `inbox/` | 待处理的原卷、答案、音频 | 否（本地数据） |
| `workflows/` | 批次产物与台账 | 否（本地数据） |

真实试卷、答案册、音频、批次产物都不进仓库；仓库里只有代码、契约、测试和这份文档。

## 维护约定

- 文档里的数字以代码为准；改代码改了数字，同一提交里改文档。
- 新增阶段、改阶段顺序、改闸门条件，都要同时改 `workflow/contracts.py` 与 [pipeline.md](pipeline.md)。
- 新增题型或维度，先改只读目录 JSON，再改 [knowledge-tagging.md](knowledge-tagging.md)。
- 文档不写具体批次号、题号、学校名，示例一律抽象成"某批次""某完形组"。

## 相关代码

| 文件 | 作用 |
| --- | --- |
| `workflow/contracts.py` | 16 个阶段的唯一契约（id、类型、依赖、产物） |
| `workflow/runner.py` | 初始化、执行阶段、人工标记、闸门判断 |
| `workflow/manifest.py` | 台账 `manifest.json`、哈希、失效、状态推导 |
| `workflow/auto.py` | 扫描、初始化、推进、`_queue.json` |
| `workflow/checks.py` | 18 项本地体检的调度 |
| `workflow/structure_checks.py` | 七项结构核对与证据绑定 |
| `workflow/structure_gate.py` | 结构自动闸门：去噪+元信息+按题型结构化+自检 |
| `workflow/structure_ai.py` | 结构 AI 复核：判定解析版答案行，校验后重跑结构化并移入附录 |
| `workflow/denoise.py` | 正文去噪与卷名/年份/时长等元信息抽取 |
| `workflow/question_content.py` | 逐题内容边界的机器问题码 |
| `workflow/review.py` | 审核放行规则 |
| `workflow/independent_review.py` | 快照绑定 + 签名 + 20% 抽样 |
| `workflow/report.py` | 逐题通报与四态统计 |
| `workbench/taxonomy.py` | 27 主类、子类、难度、分类、知识点叶子校验 |
| `workbench/media_audit.py` | 图片 verdict 判定 |
| `workflow/doctor.py` | 自检命令 `python -m workflow.doctor` |
