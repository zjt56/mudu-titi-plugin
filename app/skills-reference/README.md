# skills-reference —— 录题技能源文参考摘要

本目录整理木牍英语题库录入技能的部分规则，供本工作流对齐口径。这里是人工整理和修订的摘要，既不是原始全文，也不因放入本目录而自动成为高于用户要求、代码或服务端事实的权威。

## 在线来源与固定版本

**初版**通过 `gh api repos/mudujiaoyu/Mudu-English-Upload/contents/<path>?ref=78e2cef895a850755422c6d3c97dd73505c49e74` 在线读取，未克隆或下载源仓库。

**2026-11-11 课时强化轮**：为补齐课时录题的面版/配方/返工点，改为浅克隆源仓库核对 `main@abf73336c7127156843bee082d95096a4608f59f`（`metadata.version` 0.3.37，2026-11-03）。以下链接固定在该提交；不能用未来 HEAD 内容代替本次证据：

- [SKILL.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/abf73336c7127156843bee082d95096a4608f59f/SKILL.md)：六步总览、约束总表引用、画线专项、改动方向。
- [workbook-recipes.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/abf73336c7127156843bee082d95096a4608f59f/references/workbook-recipes.md)：课时作业逐题型配方、提示标记（D27）、同题真题（D28）、表格材料、必跑闸门。
- [layout-matrix.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/abf73336c7127156843bee082d95096a4608f59f/references/layout-matrix.md)：各题型「数据怎么写 → 页面才好看」的逐条规约与一错就变形的点。
- [constraints.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/abf73336c7127156843bee082d95096a4608f59f/references/constraints.md)：A–G 全量约束（本目录见 [constraints.md](constraints.md) 摘要）。
- [stages/01-extract.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/abf73336c7127156843bee082d95096a4608f59f/references/stages/01-extract.md)、[04-knowledge.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/abf73336c7127156843bee082d95096a4608f59f/references/stages/04-knowledge.md)：文本层体检/表格网格/子题写法、按题型维度。
- 早期来源项（知识点/目录/维度映射）仍固定在下述初版提交，未在新提交逐一复核。

初版（`78e2cef…`）读过的条目：

- [SKILL.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/78e2cef895a850755422c6d3c97dd73505c49e74/SKILL.md)：知识点阶段、全局约定及约束引用。
- [knowledge-tagging.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/78e2cef895a850755422c6d3c97dd73505c49e74/references/knowledge-tagging.md)：题组/子题落点、听力/阅读设题角度、叶子落库。
- [type-knowledge-map.json](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/78e2cef895a850755422c6d3c97dd73505c49e74/references/type-knowledge-map.json)：`by_type.*.dims` 规定的维度范围；旧 `defaults/hints` 只是历史候选提示。
- [constraints.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/78e2cef895a850755422c6d3c97dd73505c49e74/references/constraints.md)：已读完整 C 与 C+、后续段和文末后置条目，不以截断输出判断内容。
- [knowledge-matching-rules.md](https://github.com/mudujiaoyu/Mudu-English-Upload/blob/78e2cef895a850755422c6d3c97dd73505c49e74/references/knowledge-matching-rules.md)：分支匹配细则，其中部分模板与上述来源冲突。

## 文档索引

| 文档 | 内容 | 对应技能源路径 |
| --- | --- | --- |
| [workbook-recipes.md](workbook-recipes.md) | 课时作业逐题型配方、粒度判据、提示标记/同题真题、表格材料与常见返工点 | `references/workbook-recipes.md`、`references/layout-matrix.md`、`references/constraints.md`(D/E/F) |
| [knowledge-matching-rules.md](knowledge-matching-rules.md) | 七维度匹配提示、修正后的分层模板与边界 | `references/knowledge-matching-rules.md`、`references/knowledge-tagging.md` |
| [knowledge-catalog.md](knowledge-catalog.md) | 七维度结构摘要；522 节点含父节点，并非叶子数 | `references/knowledge-catalog-1.0.md`、`references/knowledge-catalog.json` |
| [type-taxonomy.md](type-taxonomy.md) | 27 个主类、子类与判定线索摘要 | `references/type-subtypes.md`、`references/filter-framework.json` |
| [type-knowledge-map.md](type-knowledge-map.md) | 每题型维度范围与题组/子题解释 | `references/type-knowledge-map.json`、`references/knowledge-tagging.md` |
| [constraints.md](constraints.md) | A–G 约束节选，标明来源记录与本次解释 | `references/constraints.md` |
| [lessons-learned.md](lessons-learned.md) | 按主题精编的历史问题及处置，不能用最大编号算条目数 | `.learnings/ISSUES.md`、`.learnings/IMPROVEMENTS.md`、`.learnings/CHANGELOG.md` |

## 来源冲突与本次决定

适用层级：本次用户明确要求及记录的项目决定优先确定修改目标；固定提交的 `by_type.dims` 用于核对维度范围，打标说明用于解释题组/子题落点，目录数据用于精确名称、路径和叶子；旧匹配模板与 `defaults/hints` 仅作线索。运行代码描述当前行为，不能由文档直接宣称已改变；服务端约束以实际接口与可核验证据为准。发现冲突须列明并形成明确决定，不能笼统宣称“本目录/技能覆盖一切”。

| 历史原文或摘要中的冲突 | 本次采用的决定 |
| --- | --- |
| 旧匹配细则要求语法选择“必须有一个语法点”；旧完形模板还带阅读技能 | 完形/语法选择题组仅体裁＋主题；整组题型子题 `knowledge=[]`，不逐小题落词汇/语法考点。组级不加答案考点或设题角度。原打标说明只明确举子题词汇，本次按用户 2026-11-05 口径收口，不能伪称是原文逐字规定。 |
| C14 把完形与其他填空并列，括注“再加词汇/语法/答案考点”，与 C9、机器表冲突 | 分层处理：完形题组仍仅体裁＋主题；其他填空按该题型范围及实际空位考点处理，不能由括注扩张组级维度。 |
| 旧写作模板或 hints 建议另挂语用、语法 | 书面表达按 `by_type` 只取体裁＋主题。 |
| 旧文分别称“1–3”“通常 2–4”“同维度最多一个”，同时又要求组级汇总全部角度 | 这些数量是历史精简建议，不能作为统一硬上限。按实际材料与题组范围校验；组级可汇总多种角度，整组题型子题 `knowledge=[]`，独立单题才写对应考点。 |
| 显示规则写“根以下各级”“一级主题＋叶子”，A15/全局约定又要求只存叶子 | 界面可展示祖先链，实际提交只存叶子；父节点不重复入标签。 |
| 旧文字称 514 节点，本地目录是 522；摘要曾把后者说成叶子数 | 本地机器目录 522 节点包含父节点，叶子须按实际树结构计算；不将旧数字或分支数量冒充叶子数。 |
| 摘要把 `谚语/习语` 改成了顿号 | 保留目录完整名称 `谚语/习语` 与路径 `语用/谚语/习语`；不能按路径末段重建名称。 |
| 旧摘要称 ISSUES 有 105 条 | 固定提交实际有 121 个编号二级章节且编号重复；本摘要不再写易误解的总条数。 |

固定提交的 C+ 仅 C11–C15，无 C21–C26；不得以不存在的条目证明后续分层规则。其他旧文问题（如难度基准与按可观测特征评估相冲突、表格兜底与去重）仍需各自依据明确决定，本次未对这些领域重新制定规则。

## 本地数据与实现集成

本地机器数据位于 `references/`：题型框架、知识点目录、维度映射及难度分类。数据条目不随摘要改名；升级需同时核对固定来源、目录和代码。

本地已在 [workbench/taxonomy.py](../workbench/taxonomy.py) 实现共用分层校验：`check_labels` 供部分草稿，`check_unit_dims` 动态覆盖 27 类组级范围，`check_question_labels` 严格递归检查最终单元/子题。[workflow/label_audit.py](../workflow/label_audit.py) 提供 `inspect_batch`、`inspect_file` 和 `python -m workflow.label_audit --batch path`，输出定位错误与批次/三份目录 SHA-256；已登记为第 17 项本地体检。runner 的审核闸门重新读取实际最终批次并合并审计结果，不通过即阻断；不是 `review.evaluate` 直接调用。草稿允许部分标签并返回 warnings，不能用保存成功代替最终严格检查。自动检查覆盖目录、层级、scope、必带项与 ID 一致性，原题语义仍需复核。细节见 [知识点与标签口径](../docs/knowledge-tagging.md)。
