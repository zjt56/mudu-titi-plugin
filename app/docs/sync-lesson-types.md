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

## 三、语音组题的原生内容与 A8/A9（2026-11-11）

语音整大题保持 `group`。原卷 `[IMG image2.jpeg]` / `[IMG image3.jpeg]` 经规范化进入 `attachments(kind=image, placement=question)` 后，`material` 为空是原生表示；不得重插图片标记、编造「见附图」或改为单独题。大题作答导语逐字进入组级 `stem`，从当前正文的唯一大题坐标读取，不能借用另一节标题。

`phonetic_structure.verified_groups/shape` 只为当前来源完整复建的 scoped-v1 语音组提供证明：大题标题/结束坐标由 `heading_candidates` 独立重建；窗口须覆盖整节非空正文及全部原号；组、子题、原生图片和来源身份必须精确对应；图片须在本批媒体目录真实存在且归属唯一。只有共享图片、或确无共享材料而子题题干完整的语音组可免文字材料要求；无印刷号纯图组的单个内部兼容别名可空 child stem，答案仍必填。无来源上下文、跨节/漏题、伪图片/异批 URL、缺图、空图、子题改写仍拒绝；不是所有 group 的豁免。

生成、原文审计、原卷预览、assemble 临时候选预检、发布检查和 payload 共用此口径；payload 证明上下文由调用方显式提供，不采信候选自报路径。本地 `source_span/printed_numbers` 证明字段明确排除出远端请求，其他未知字段仍严格检查。并排小题的最终题干恢复沿用 scoped 解析，防止把左侧小题重新拼到右侧。来源刷新沿用既有内容哈希协议，保留已验证答案/知识点，不以新增坐标字段造成历史状态哈希冲突。

回归：`workflow.test_phonetic_structure` 覆盖来源切片 → draft → assembly → 原生 payload / `source_blocks`，以及临时候选显式来源、边界/漏题/图片负例；实际课时副本验证 27 单元、41 答案、27 知识点单元、教材字段 0。

## 四、真实课时标题 → 目录题型（回归基线）

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

### 5.1 按桌面《英语题型汇总.xlsx》表一对齐（2026-11-11）

表一（`题型汇总`）是题型/子类/组单的最终口径，逐处明细与插件规则都要向它看齐。本轮改判 15 行：

| 原卷标题族 | 旧口径 | 表一新口径 | 规则 id |
|---|---|---|---|
| 一句一空语法功能词（人称代词 / do、does / 介词 in、on、under / 连词 and、but、because / 适当的介词 / How、What / for、of / if、whether、that / 关系代词） | 单词拼写·句意 | 选词填空·多句（仍单独题） | `spell-grammar-word` |
| can/can't/could/couldn't 的适当形式 | 单词拼写·句意 | 选词填空·多句（组题） | `spell-modal` |
| 看图补全句子（图片提示 / 家谱 / 地图 / 图示 / 图片信息） | 单词拼写·看图填词 | 完成句子·补全句子（组题） | `sentence-picture` |
| 短文后完成思维导图 | 任务型阅读·信息摘录 | 任务型阅读·阅读综合 | `task-reading-mindmap` |
| 填入恰当的特殊疑问词或词组，完成对话 | 补全对话·（正文推断） | 补全对话·填写单词 | `dialogue-word` |

- `补全句子` 是**本地补入** `app/references/filter-framework.json` 的 `完成句子` 子类（平台原框架无）；
  2026-11-11 已同步登记到平台侧 `static/filter-framework.json`，安装体检 A7 与平台
  `validate_upload_question_types` 均接受该子类。
- `sentence-picture` 必须排在 `spell-picture` **之前**（顺序即优先级），否则「家谱 / 根据地图 / 图示」先被
  看图填词命中；「根据首字母及图片提示完成单词」「图片中的节日」等仍走 `spell-picture`。
- `dialogue-word`（特殊疑问词填一格）必须排在 `dialogue` **之前**；表一 78 行里只有「完形填空（10空）」
  与「补全对话（7选5）」必须靠正文判定，其余标题即可判到题型/子类。

## 六、边界（不要误读）

- 这些规则只用于**课时同步材料**；单元卷、期中期末、月考、中考卷不得套用（`looks_like_sync_practice` 判定）。
- 规则只决定题型/子类/组题口径，**不改原文、不改题号、不合并不同大题的同号题**。
- 审核页显示修复只动展示层与缺失回填，不覆盖已判定标签；人工审核差异仍按规则 6 留给人判断。
