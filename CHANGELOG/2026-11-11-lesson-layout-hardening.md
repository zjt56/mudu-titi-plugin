# 课时录题技能强化：版面/选项/空壳子题硬检查 + 提示词与源参考

- 日期：2026-11-11
- 触发：用户「学习该技能中关于课时这方面的一些录题的一些 Skill，一些提示词，一些约束，怎么提取的，怎么让大模型去做，然后强化到这个插件中，特别是一些细节方面的东西」（对方仓库 `mudujiaoyu/Mudu-English-Upload`）。

## 一、背景

远端技能 `Mudu-English-Upload`（`main@abf7333`，version 0.3.37）把课时作业（同步练习册）的录题经验拆成 `SKILL.md`、`references/workbook-recipes.md`（逐题型配方）、`references/layout-matrix.md`（「数据怎么写→页面才好看」的逐条版面规约）、`references/constraints.md`（A–G 全量约束）与 `.learnings/` 踩坑台账。

本插件此前已有同步练习的**题型/组题判定口径**（`sync-practice-rules.json` + `sync_practice.py`，硬规则 25），但**版面/字段写法层面的细节**只零散存在于 `audit_type_layout.py` 的 E20/D7/D10 三条，很多「一错就整屏变形」的返工点没有被确定性代码兜住，提示词里也没有把课时版面规约讲透。本次把远端技能里最值钱的「细节」强化进代码与提示词。

## 二、改了什么

| 文件 | 变化 |
|---|---|
| `app/scripts/06-upload/audit_type_layout.py` | 从「E20 选项条数 + D7 + D10」扩为**六类硬检查**：①E20 选项条数（保留，覆盖听力/单选/阅读/补全/完形）；②**E20 选项字母连续**——A→… 不跳号不重复（Claude 的 `A/B/D/D` 这类会被点名）；③**C11 完形单元级 options 必须留空**（选项挂子题）；④**C12 选词/短文/教材语篇填空子题不得带选项**；⑤**D7/SHELL_SUB 材料已带齐题号时子题 stem 只能空壳**（纯 `______`/纯题号），写整句或重复题号都报；⑥D10 组题粒度不变。新增 `_material_numbers`/`_option_letters`/`_check_option_continuity`/`_check_shell_subquestions`，材料题号识别支持 `__N__`/`　N　`/`（N）`/`N.` 等写法 |
| `app/workflow/test_audit_type_layout.py`（新增） | 7 条回归：完形单元选项留空、选项字母不连续被拦、填空子题带选项被拦、补全对话子题重复整句被拦、干净批次通过、材料未带齐题号不误报 |
| `app/workflow/sync_practice.py::prompt_block` | 给模型的下发提示增加**版面规约段**（题干在材料上方、空档 `______` 与首字母紧贴、材料带齐题号时子题只写空壳、完形子题写题号 4 选项、填空子题无选项、补全对话 A–G、完成句子一词一空、阅读保留篇章标号）与「没见过的题型照原卷版式录、同图多小题合成 group」 |
| `app/prompts/roles/knowledge.md` | 「脑子检查」补 3 条按题型的维度约束：**阅读/任务型/读写综合每道子题都要一个设题角度**；**完形/语法选择只挂体裁+主题**（不挂角度/词汇/语法，C9/C25）；**填空拼写类不得挂主题**（单词拼写只有词汇/语法/语音、完成句子只词汇/语法、翻译词汇/语法/语用） |
| `app/skills-reference/workbook-recipes.md`（新增） | 课时作业逐题型配方的**节选摘要**：粒度判据、逐题型字段写法、提示标记（D27）与同题真题（D28）、表格材料四步、五个「一错就整屏变形」的点、本地对应检查命令 |
| `app/skills-reference/README.md` | 固定版本补 2026-11-11 浅克隆 `main@abf7333`（v0.3.37）证据与新增来源链接；文档索引加 `workbook-recipes.md` |
| `app/docs/README.md` | 文档索引补 `sync-practice.md` 与 `skills-reference/workbook-recipes.md` 两行 |
| `CHANGELOG/RULES.md` | 追加同日规则台账条目 |

## 三、没改什么 / 边界

- **不改判定口径，只加确定性检查**：`audit_type_layout` 只对「字段写法/选项/空壳」做硬检查，不改变题型/组单结论；`sync-practice-rules.json` 与 `type_kind` 一字未动。
- **不套用到非同步练习材料**：`prompt_block` 的版面段仍在 `looks_sync_practice` 为真时才注入；`audit_type_layout` 按 `question_type` 精准命中，不受卷型影响。
- **不复用远端与用户规则冲突的内容**：远端 F7「缺答案/解析/大意自己补并标 `（AI生成，待校对）`」与本插件用户口径（缺就不生成，填「无」）冲突——**不引入**，`workbook-recipes.md` 摘要不收录 F7 生成规则，只收版面/配方细节；`knowledge.md` 只加维度约束，不涉及 AI 生成。
- **不改历史批次**：新检查只作用于后续批次与本地体检；旧批次不回头重跑。
- **不新增题型/子类**：一律沿用目录原值。

## 四、如何验证

```
cd app
python3 -m unittest workflow.test_audit_type_layout          # 7 tests OK
python3 -m unittest workflow.test_sync_practice workflow.test_sync_practice_integration
python3 -m unittest workflow.test_agent_tasks workflow.test_sync_lesson_extraction \
                  workflow.test_structure_gate workflow.test_table_spans_and_pdf_answers
# 合计 138 tests OK（含上述）
```

全量 `python3 -m unittest discover -s workflow -p 'test_*.py'` 结果见提交说明。

冒烟：对合成的脏批次（补全对话缺题号/子题重复整句、完形选项 `A/B/D/D`、完形单元带选项、选词子题带选项）逐条报 `[E20]/[D7]/[C11]/[C12]` 并退出码 1；干净批次（补全对话/完形/单选/阅读/选词）退出码 0。

## 五、回退方式

- 删除 `app/workflow/test_audit_type_layout.py`、`app/skills-reference/workbook-recipes.md`；
- `git checkout -- app/scripts/06-upload/audit_type_layout.py app/workflow/sync_practice.py app/prompts/roles/knowledge.md app/skills-reference/README.md app/docs/README.md`；
- 删除 `CHANGELOG/RULES.md` 对应条目与本文件。
