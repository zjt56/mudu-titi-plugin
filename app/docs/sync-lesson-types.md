# 同步课时题型匹配规则（同步练习册 / 课时 A 计划）

本文件是**课时同步材料**（同步练习册、课时作业、单元课时训练）的题型与结构口径说明，与 `app/docs/sync-practice.md`（规则总览与数据来源）互补：
`sync-practice.md` 讲规则怎么来、JSON 怎么维护；本文件讲**真实课时卷长什么样、审核页怎么展示题型、遇到新表述怎么加**。

机器可读口径仍以 `app/references/sync-practice-rules.json` 为唯一来源，代码入口 `app/workflow/sync_practice.py`。

## 一、课时同步 ≠ 中高考卷

| 特征 | 中高考卷 | 课时同步（本文件适用） |
|---|---|---|
| 题号 | 整卷连续编号 | **每个罗马数字大题从 1 重新开始**，不算冲突 |
| 段落 | 一卷到底 | 常见「基础段 + 提升段」，提升段又从 Ⅰ 开始 |
| 小题排布 | 一题一行 | 常见**一行两个小题**（`1.U　v (　　　)　　　　2.W　x (　　　)`） |
| 小题定位 | 行首题号 | 可能带说话人前缀（`C:1.<u>`）或只有图片（`[IMG image2.jpeg]`） |
| 答案解析 | 独立答案册/文末 | 常紧跟最后一个大题，标题「参考答案」，答案行也用 `1.`/`1~5 ABC` |
| 题型 | 全卷统一题型 | 同一课时里语音题、单词拼写、完成句子、单项填空、补全对话、阅读理解混排 |

因此课时卷的题号重复、图片题、答案区紧邻都不是异常，**只要每个原题位置唯一绑定到自己的大题窗口**，位置性代码一律只作「已核对」记录，不阻断（见 `app/workflow/structure_gate.py`）。

## 二、审核页必须直接展示题型

审核页（`lib/client.js` 的 ReviewView）每个单元的「题型」一律显示**目录题型 + 子类**：

- 列表行：`语音题`、`单词拼写 · 汉语及首字母`、`阅读理解`；大题标题只作悬停说明，**不得顶替题型**。
- 详情标题栏：显示目录题型、子类及「组题 / 单独题」。无印刷小题号的图片组题明确显示「原卷未标小题号」，内部兼容编号不能充当真实小题数。
- 优先保留审核行已经生效的题型与子类（包括已保存的人工修改）；只有字段缺失时才用确定性规则反查（`type_from=section_rule`），不覆盖既有判定。缺失且无可靠规则时显示「未判定」，不得回退为题干或大题标题。
- 子类判不出就留空，不得自造；`语音题` 在目录里**没有子类**，字母类题目只能记 `语音题`。

回填落点：

- `app/workbench/server.py::decorate_review_rows`——仅对课时同步卷、且字段确实缺失的行，用本行大题标题 `sync_practice.classify()` 回填 `type`/`subtype`/`subtypes`。
- `app/workflow/structure_ai.py::sync_subtype`——结构归类时按包含该窗口的大题标题回填子类，题型不一致不回填。

## 三、真实课时标题 → 目录题型（回归基线）

以下 9 条来自 `01七上人教版课时A计划StarterUnit1Hello!第一课时` 真实原卷标题，`app/workflow/test_sync_practice.py::TestLessonHeadingsMatchesRulesDocument` 逐条断言：

| 原卷大题标题 | 题型 | 子类 | 组题/单独题 |
|---|---|---|---|
| Ⅰ.按照字母表顺序,写出空白处所缺字母 | 语音题 | — | group（小题在图片里） |
| Ⅱ.按照名字的字母顺序为下列人物排序,并将句子补充完整 | 语音题 | — | group |
| Ⅲ.根据首字母及汉语提示补全单词 | 单词拼写 | 汉语及首字母 | standalone |
| Ⅳ.根据汉语意思完成句子,每空一词 | 完成句子 | 汉语提示 | standalone |
| Ⅴ.单项填空 | 单项填空 | 四个选项 | standalone |
| Ⅰ.判断下列各组字母的大小写是否一致,一致的写T,不一致的写F | 语音题 | — | group |
| Ⅱ.找出下列句子中书写错误的单词并改正 | 改错 | 句子改错 | standalone |
| Ⅲ.补全对话 | 补全对话 | — | group |
| Ⅳ.阅读理解 | 阅读理解 | — | group |

## 四、结构性边界（别再踩）

1. **卷末答案区不属于最后一个大题**：`sync_practice.answer_key_start()` 先定位「参考答案/答案」标题（要求其后 40 行内至少 2 行答案行，且后面没有真实课时、Unit 或新大题；中间课时答案不能截掉后续正文），`heading_candidates` 与 `extract.infer_structure` 都把末节窗口截到答案区前一行。否则答案行的 `2.`/`3.` 会撞进正文题号，报 `SOURCE_DUPLICATE_NUMBERS`/`QUESTION_NOT_FOUND`/`SOURCE_OPTIONS_INCOMPLETE`。
2. **并排小题要成组空档**：同一行第二个题号必须跟在本行成组空档（`____`、两个全角空格、`(　　)`）之后，且题号严格递增才认；散文里出现的 `2.` 不算。
3. **说话人前缀**：仅 `[A-Z]{1,2}:` / `[A-Z]{1,2}：` 接题号才认（`C:1.<u>`），其它冒号不动。
4. **纯图片小题**：窗口内正文仅含 `[IMG …]`、没有文字题干时记 `image_only` + `IMAGE_SECTION_UNNUMBERED`（informational）。必须保留完整来源单元；无可读印刷题号时用内部别名并明确 `printed_numbers=[]`，不能按上一节猜题号。只有完整单元存在才豁免号码识别；整节缺失仍报 `SOURCE_SECTION_UNCOVERED`。表格里有文字不能按纯图豁免。
5. **题组图片完整展示**：第一大题字母表填空与第二大题姓名排序分类都按 group 保留原图。预览、媒体审计及详情 payload 必须消费已验证来源窗口；不能因图片在首个印刷题号之前或长宽比大而隐藏。显式排除仍优先，过期/冲突来源不提升为题目图。回归覆盖 `source_slice → source_blocks → detail → HTTP JSON`。
6. **课时号重排**：`NUMBERING_RESTART`、`GROUP_BOUNDARY_REVIEW`、`ANSWER_APPENDIX_DETECTED` 在无未解决问题时只作「✓ 已核对」记录。
7. **答案附录按完整大题骨架关联**：只有单一课时上下文、正文所有罗马大题及答案分段顺序完全对应、当前来源窗口有效时，才按大题出现序次关联答案；基础/提升的同号不能全局借用。答案仍须有真实文件与行坐标；缺段、重复题号、冲突值、错坐标与伪报缺失均拒绝。`answers_direct` 仅在全部客观题完整唯一时经正常 export/import 交付，模型调用为 0；缺项返回普通受控路径，不制造缺失占位，不绕过局部修复范围。

## 五、新增表述怎么加（固定流程）

1. 在 `app/references/sync-practice-rules.json` 加规则（**顺序即优先级，先命中先返回**；`any`/`none`/`subtype_any` 三种匹配）。
2. 在 `app/workflow/test_sync_practice.py` 加用例；真实原卷标题同时进 `LESSON_HEADINGS`（本文件第三节的表同步更新）。
3. 跑 `PYTHONPATH=app python -m workflow.sync_practice`（自检）与 `python -m unittest workflow.test_sync_practice workflow.test_extract workflow.test_sync_practice_integration`。
4. 真实批次复验：`python -m workflow.runner run --batch-dir <批次> --stage extract --force`，再 `--stage structure_gate --force`；已完成阶段不加 `--force` 不会重跑。
5. 记 `CHANGELOG/`（现象/改动/边界/验证数字/回退），卡点用 `python -m workflow.issue_log add|fix` 落 `ISSUES/`。

## 六、边界（不要误读）

- 这些规则只用于**课时同步材料**；单元卷、期中期末、月考、中考卷不得套用（`looks_like_sync_practice` 判定）。
- 规则只决定题型/子类/组题口径，**不改原文、不改题号、不合并不同大题的同号题**。
- 审核页显示修复只动展示层与缺失回填，不覆盖已判定标签；人工审核差异仍按规则 6 留给人判断。
