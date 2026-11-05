# 打标签：四个子智能体

设计图（`英语题库录题流程设计.svg`）把打标签拆成四个并行的子智能体，对应四个独立阶段、四份产物：

| 子智能体 | 阶段 | 负责 | 产物 |
| --- | --- | --- | --- |
| 1 | `explanations` | 解析匹配、文章大意匹配；缺失填「无」，不生成 | `04_knowledge/explanations.json` |
| 2 | `knowledge` | 知识点、教材知识点匹配（逐字沿用子智能体1的解析与大意） | `04_knowledge/knowledge.json` |
| 3 | `types` | 题型与子类、题目词数、题目内容是否完整 | `03_types/types.json` |
| 4 | `answers` | 答案提取、答案与解析的对应整理、难度判定 | `01_source_ans/answers.json` |

四路都完成后才进入组装；一审全量盲判重判这些标签（含知识点、考点关系与难度），二审按 20% 抽样复核题面/答案/标签与原文的对照：整组题型整组抽取、只做组级核对，其余题型按小题抽。图中「AI审核」统指后面的一审、二审，不是额外的第五个环节。

**执行时的依赖（2026-11 实测）**：`types`、`answers` 依赖结构确认，`explanations`、`knowledge` 依赖已通过的 `types` 与 `answers`（`knowledge` 还会逐字沿用 `explanations` 的解析与大意）。`workflow.auto` 只并发启动依赖已就绪的阶段，因此实际是**第一波 3 路并发（answers/audio/types）→ 第二波 2 路并发（explanations/knowledge）**，已是现有校验前提下的最大并行。若强行让四路同时启动，解析与知识点将看不到已验证的题型与答案，只能产出未经校验的草稿；草稿仍需在依赖就绪后重新执行，不会节省真实调用，反而增加失败面。

## 子智能体1：解析与文章大意

1. **解析匹配**：按真实题号、章节和材料边界，在原卷正文、附录、答案册中定位该题原解析，逐字保留（去掉「【解析】」等字段标题）并给出定位证据。有原解析时不得替换。
2. **文章大意匹配**：按实际篇章及其所属题号匹配原【文章大意】，逐字保留并记录来源位置；重复的 A/B 篇号不是唯一标识。大意只写组级一份，不在每个子题重复。
3. **缺失处理（用户规则）**：确认已提供来源确实没有该题解析或该篇文章大意时，对应字段一律只填「无」，**直接跳过，不再使用 AI 生成、不补写、不推断、不加任何来源标记**。

边界：非听力题组每个子题各自填（缺失填「无」）；独立题缺失也填「无」；听力不写内容解析，缺失同样填「无」；不得串用其它材料的解析或大意。字段不得留空——缺失必须显式写「无」。

```json
{"items":[{"unit_id":"q-1","explanation":"无",
  "articleSummary":"无","subquestions":[],"evidence":["正文第1题；附录第1题仅给答案，无原解析/原大意"]}]}
```

## 子智能体2：知识点与教材匹配

只打知识点：主类、子类、目录叶子与 ID。

教材位置（`textbook`）只用于**课时同步练习类来源**（`同步练习`、`单元测验`）：当原卷标题或卷内章节本身就写明所属教材单元时，按 `version/book/unit/section` 逐字填写并附原文证据。中考、期末等普通试卷不匹配教材，`textbook` 留空；不得按话题相似度猜测，也不得编造名称或 ID。目录数据见 [textbook-catalog.md](textbook-catalog.md)。

**不生成、不改写内容**：`answer` 逐字取自 `01_source_ans/answers.json`，`explanation`/`articleSummary` 逐字取自 `04_knowledge/explanations.json`；任何差异都会被导入器拒绝。

## 子智能体3：题型、词数、内容完整

`type`/`subtypes` 只能取当前目录值；`words` 是该单元自身题干、材料与选项的英文词数；`contentComplete` 在题干与材料不匹配、选项缺失或内容被截断时为 `false`。

## 子智能体4：答案、解析整理、难度

`answers` 逐题号从答案原文提取，不猜不补；`difficulty` 依据可观察特征（长度、句式、选项设计、考查点）判定并给出 `difficultyEvidence`，不得只按题型硬套。

## 校验与流转

导入时依次校验：单元与子题完整覆盖、知识目录叶子与维度、答案沿用、内容归属（`upload_content` 按题号锚点判定可溯源或已标注，跨材料串用直接拦停）、阅读与完形类必须有文章大意、知识点不得改写内容。装配不得改写这些字段。

```bash
python3 -m workflow.agent export --batch-dir workflows/<批次> --stage explanations
python3 -m workflow.agent import --batch-dir workflows/<批次> --task-id <task_id> --result <result.json>
python3 -m workflow.agent run --batch-dir workflows/<批次> --stage explanations
```

来源、规则、答案或题型变化会使旧任务包失效；解析产物变化会使知识点、装配和后续审核失效。生成标注与机器检查只说明来源声明、覆盖和结构符合契约，不代表生成内容已通过真实语义审核。
