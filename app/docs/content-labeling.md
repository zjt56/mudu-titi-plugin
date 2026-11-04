# 打标签：四个子智能体

设计图（`英语题库录题流程设计.svg`）把打标签拆成四个并行的子智能体，对应四个独立阶段、四份产物：

| 子智能体 | 阶段 | 负责 | 产物 |
| --- | --- | --- | --- |
| 1 | `explanations` | 解析匹配、缺解析生成、文章大意匹配、缺大意生成 | `04_knowledge/explanations.json` |
| 2 | `knowledge` | 知识点、教材知识点匹配（逐字沿用子智能体1的解析与大意） | `04_knowledge/knowledge.json` |
| 3 | `types` | 题型与子类、题目词数、题目内容是否完整 | `03_types/types.json` |
| 4 | `answers` | 答案提取、答案与解析的对应整理、难度判定 | `01_source_ans/answers.json` |

四路都完成后才进入组装；一审重判这些标签，二审复核知识点，三审抽 20% 小题。图中「AI审核」统指后面的一审、二审、三审，不是额外的第五个环节。

## 子智能体1：解析与文章大意

1. **解析匹配**：按真实题号、章节和材料边界，在原卷正文、附录、答案册中定位该题原解析，逐字保留（去掉「【解析】」等字段标题）并给出定位证据。有原解析时不得用 AI 文本替换。
2. **缺解析生成**：确认已提供来源确实没有该题解析后，依据该题实际题干、选项、共享材料和已验证答案写出实质解析，末尾必须原样写 `（AI生成，待校对）`。不得编造与原文矛盾的题干或材料；依据不足返回 `needs_review`。
3. **文章大意匹配**：按实际篇章及其所属题号匹配原【文章大意】，逐字保留并记录来源位置；重复的 A/B 篇号不是唯一标识。
4. **缺大意生成**：阅读理解、任务型阅读、读写综合、完形填空、语法选择缺大意时，依据完整真实材料写成 2–3 句中文，末尾写同一标记。大意只写组级一份，不在每个子题重复。

边界：非听力题组每个子题都要有自己的解析；独立非听力题解析不能为空；听力不生成内容解析，答案自补时只允许一行 `答案由AI生成（AI生成，待校对）`；不得串用其它材料的解析或大意。

```json
{"items":[{"unit_id":"q-1","explanation":"根据原句的时间状语判断时态。（AI生成，待校对）",
  "articleSummary":"","subquestions":[],"evidence":["正文第1题；附录第1题仅给答案"]}]}
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
