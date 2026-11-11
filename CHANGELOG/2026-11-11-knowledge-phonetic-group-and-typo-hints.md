# 修复「知识点」卡点：语音题组子题可空 + 完成句子禁语用 + 路径错字提示（含装配/恢复链同口径）

- 日期：2026-11-11
- 触发：用户「卡点诊断 批次：01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio … 打标签：知识点标签匹配：阶段失败 原因：knowledge must be a nonempty list of nonempty strings」；随后「需要，并且把这个错误也加强到代码层面，然后增强约束」。

## 一、背景 / 卡点判定口径

批次 `01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio` 的知识点（knowledge）阶段失败，错误 `knowledge must be a nonempty list of nonempty strings`，反复重试同一报错。

定位（复现 `agent_tasks.accept_result(..., 'knowledge', <候选>)` 逐步追踪）：
1. 该批次有 3 个**语音题组**（「按字母表顺序写所缺字母」「按名字字母顺序排序并分类」等，子题 1–8）。
2. 语音题是**整组题**，知识点只有「语音」一个维度，正确写法是**组级一个**（`语音/基本读音/26个字母的读音`），子题 `knowledge=[]`——模型正是这么返回的，**正确**。
3. 但 `agent_tasks._validate` 的知识点前置校验里，"子题可空"只认 `taxonomy.whole_unit_type()`（该表不含「语音题」，因它不在 `WHOLE_UNIT_TYPES`），于是语音题被当逐题打标，子题 `[]` 触发 `_strings(..., nonempty=True)` → 报错。
4. **权威校验器** `taxonomy.check_question_labels` 用 `is_group = 有子题 or unit_kind==group`，本就允许组内子题空——两处口径不一致，前置校验写窄了。

证据：修好前置校验后，此前已生成的合格候选 `knowledge-retry-mau43j80` 直接 `accept_result` 通过（`completed=True`），确认卡点是误判而非内容错误。

顺带暴露的**真实内容问题**（模型打标错误，非代码）：某「完成句子」挂了 `语用/情景交际`（该题型只允许 词汇/语法）、某「阅读理解」主题路径写成 `社会服务与人沟通`（目录实为 `社会服务与人际沟通`）。这两条本就被 `check_labels` 拦，只是加了更可定位的提示。

## 二、改了什么

| 文件 | 变化 |
|---|---|
| `app/workflow/agent_tasks.py` | ①**知识点阶段**前置校验：`whole` → `whole or is_group`（有子题或 `unit_kind=group`），与 `taxonomy.check_question_labels` 的 `child_of_whole` 同口径，语音题等整组题子题可空；②**装配阶段**同一处校验同步改为 `whole_unit or is_group_unit`（否则装配重跑仍会撞同一误判）；③**提示词** `RULES["knowledge"]` 增补：语音题是组题（组级「语音」维度、子题空）、按题型**维度范围**速记（完成句子/改错只 词汇·语法，禁语用/主题；单词拼写 词汇·语法·语音；单项填空 +语用；补全对话 体裁·主题·语用；完形/语法选择/书面表达 仅 体裁·主题…）、**路径笔误**要求逐字复制目录叶子（举例 `社会服务与人沟通` → 应为 `社会服务与人际沟通`） |
| `app/workbench/taxonomy.py` | `_check_labels` 对未解析路径给出**最近目录叶子**提示（`_closest_leaf`，difflib，cutoff 0.78），只提示不自动纠正、不放宽校验；以便模型/运维一次改对笔误 |
| `app/workflow/question_content.py` | `restore_source_stems`：对**材料已带齐每个空位题号的组题（补全对话等）**，子题题干强制 `______`（D3/D25），不再把 `audit_source_unit` 解析出的「后一行对白」抄进子题（否则与材料重复、被 D7 拦）。新增 `_material_blank_numbers` 识别 `__N__`/`N.____`/`　N　____` 空位号 |
| `app/prompts/roles/knowledge.md` | 「常见错误」补：完成句子/改错挂语用、单词拼写挂主题、主题路径错字 |
| 回归测试 | `test_agent_tasks`：语音题整组子题可空被接受；`test_label_audit`：语音题组子题可空、完成句子挂语用被拒、主题路径笔误报相近叶子；`test_question_draft`：补全对话材料带号时子题题干停留 `______` |
| `CHANGELOG/RULES.md`、根 `AGENTS.md`？ | 本次为缺陷修复+提示强化，落 `RULES.md` 台账一条；不改硬规则编号 |

## 三、没改什么 / 边界

- **不放宽校验**：语音题组子题可空是**纠正误判**（与权威校验器一致），不是放宽；完成句子禁语用、主题路径必须命中目录叶子仍严格阻断，只加「相近叶子」提示，不自动替换。
- **不改判定口径**：题型/子类/组单结论不动；`sync-practice-rules.json`、`WHOLE_UNIT_TYPES` 均未改。
- **不改历史批次**：仅对当前批次用修复后代码重跑（knowledge 复用其已生成且通过修复后校验的候选，零额外模型调用；assemble 走确定性零模型路径）。
- **运维卡点处理**：该批次被 AI 运维（附加能力，硬规则18）接手后因 `content_retry_requires_scope` 无法整卷重生成而挂起；根因为代码缺陷，已在代码层修复，故将其遗留 incident 记录置为 `cancelled`（保留历史事件）后交由正常流程重跑，未伪造完成态。

## 四、如何验证

```
cd app
python3 -m unittest workflow.test_agent_tasks workflow.test_label_audit \
    workflow.test_assemble_local workflow.test_question_content \
    workflow.test_content_labeling workflow.test_question_draft
# 全部通过（含新增 4 条）
```

- `test_phonetic_group_accepts_empty_child_knowledge`：语音题组子题 `[]` 被接受（此前报 nonempty）。
- `test_completing_sentence_rejects_pragmatic_dimension`：完成句子挂 `语用/情景交际` 被判越界。
- `test_unknown_theme_path_reports_closest_leaf_typo_hint`：`社会服务与人沟通` 报出相近叶子 `社会服务与人际沟通`。
- `test_shared_blank_dialogue_children_keep_shell_stem`：补全对话子题题干停留 `______`。

实批复验：knowledge 由 `failed` → `completed`（复用 `mau43j80` 候选）；assemble 确定性重组装 `completed`（零模型）；meta/underlines/validate 依次 `completed`；review 已进入执行。

## 五、回退方式

`git checkout -- app/workflow/agent_tasks.py app/workbench/taxonomy.py app/workflow/question_content.py app/prompts/roles/knowledge.md app/workflow/test_agent_tasks.py app/workflow/test_label_audit.py app/workflow/test_question_draft.py`；删除本文件与 `CHANGELOG/RULES.md` 对应条目。
