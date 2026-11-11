# 课时同步卷：末节答案区误并入大题、并排小题/说话人前缀/图片题漏号，审核页拿标题充题型

- 日期：2026-11-11
- 触发：用户 m05042「检测最新任务的卡点是什么，然后给它解决优化；同时把这个卡点记录下来，写进代码规则，加强提示词约束」；m05238「这个是同步课时题型的……依据同步课时这种题型的一个特点，再给他写一篇对应的匹配规则」；m05271「录入到试卷审核页面的时候，它那个题型啊，它应该直接展示题型，而不是说题干……」。

## 背景

批次 `01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio`（`C:\Users\Administrator\.dsh\wf-plugin-data\workflows\`）在 `structure_gate` 被 20 条阻断卡死，`workflow/operations.json` 的 incident 停在 `waiting_evidence`，`self-heal.json` 记 `upstream_rederive`。同一份卷在审核页把「Ⅰ.按照字母表顺序,写出空白处所缺字母」这类大题标题显示成题型，`subtype` 全为空。

四个真实缺陷（都可确定性复现，与模型无关）：

1. `sync_practice.heading_candidates` 给最后一个大题的 `end_line` 直接取 `len(lines)`，把卷末「参考答案」区（第 121–133 行）算进 `Ⅳ.阅读理解` 的窗口；答案行行首的 `2.`/`3.`/… 被当正文题号 → `SOURCE_DUPLICATE_NUMBERS [2,3,4,5,6]`、`QUESTION_NOT_FOUND 2–6`、`SOURCE_OPTIONS_INCOMPLETE 4–8`。
2. `extract.py` 只认行首题号，`1.U　v (　　　)　　　　2.W　x (　　　)` 每行两个小题只取到 1 → `Ⅰ.判断…` 得 `[1,3,5,7]`，2/4/6/8 报 `SOURCE_NUMBERS_UNDECLARED`。
3. `Ⅲ.补全对话` 的小题号带说话人前缀 `C:1.<u>`，`QUESTION` 与 `blank_numbers` 都不认 → `questions=[]` → `QUESTION_BOUNDARY_MISSING`。
4. `Ⅰ.按照字母表顺序` 的小题只在图片里，窗口内没有文字行 → 同样报 `QUESTION_BOUNDARY_MISSING`，而按硬规则 6 图片能力受限只作 informational。

## 改了什么

- `app/workflow/sync_practice.py`：新增 `_ANSWER_HEADING`、`_ANSWER_ROW`、`answer_key_start(raw_text)`（1 基行号，要求标题后 40 行内 ≥2 行答案行，避免正文「答案：B」误切）；`heading_candidates` 把末节 `end_line` 截到答案区前一行。
- `app/workflow/extract.py`：扫描到已验证尾部答案区即停止，避免答案题号污染正文；同步课时才启用 `DIALOGUE_QUESTION` 与 `same_line_numbers()`（要求成组空档且题号严格递增）。纯图判定先于上一节推号与声明题数检查；只移除图片标记后无文字才记 `image_only=True`，表格文字不豁免。
- `app/workflow/structure_gate.py`、`question_identity.py`：纯图必须保留来源单元，未印题号用内部别名并标 `printed_numbers=[]`，不从上一节猜题号；缺少单元仍报 `SOURCE_SECTION_UNCOVERED`。规则候选与现有同坐标章节合并，完整窗口截到下节之前，不吞题组。
- `app/workflow/structure_ai.py`：新增 `sync_subtype(candidate, start, qtype)`——按包含该窗口的大题标题确定性回填子类，题型不一致或规则无子类（如 `语音题`）返回空；`_sanitize_classified` 在 scoped 单元上用它回填 `subtype`。
- `app/workbench/server.py`：新增 `_row_subtype()`；`decorate_review_rows` 改为「题型或子类缺失时」才按 `sync_practice.classify(本行大题标题)` 回填，已判定题型绝不覆盖，`type_from=section_rule` 只标在真正补出来的题型上。
- `lib/client.js`：审核列表行改为 `题型 · 子类`（大题标题只留悬停），右侧标题栏改为 `题型：… · 子类：… · N 小题`。
- `app/docs/sync-lesson-types.md`（新）：课时同步 vs 中高考卷对比、审核页必须直接显示题型、真实 9 条大题标题映射表、结构性边界、新增表述的固定流程。
- 根 `AGENTS.md` 硬规则 30；`CHANGELOG/RULES.md` 追加同名条目；`app/workflow/test_sync_practice.py` 新增 `TestLessonHeadingsMatchesRulesDocument`（9 条真实标题 + 文档覆盖 + 提示词口径）；`app/workbench/test_review_type_display.py`（新）3 条；`app/workflow/test_extract.py` 4 条；`app/workflow/test_structure_ai.py` 1 条。

## 没改什么 / 边界

- 原卷及答案原文保持不变；真实批次只通过正常阶段命令更新派生产物，不直接编辑 manifest 或制造完成态。答案区保留，只排除在最后大题窗口之外。
- 位置性代码（题号跨大题重置、分组复核、答案附录、图片题）在无未解决问题时仍只记「✓ 已核对」，**不等于放行质量问题**：`structure_gate` 只对 scoped 未解决问题阻断。
- 审核页回填只补缺失字段，不覆盖批次 `question_type` 与人工标签；人工审核差异仍按硬规则 6 留给人判断，不自动裁决。
- 规则只适用课时同步材料；单元卷/期中期末/月考/中考卷不套用。

## 如何验证

- `workflow.test_extract`（含 4 条新增）、`workflow.test_sync_practice`（21）、`workflow.test_sync_practice_integration`、`workflow.test_progressive_pipeline`：58 tests OK（另一次 `test_extract + test_sync_practice_integration` 40 OK）。
- `workflow.test_structure_ai + workflow.test_sync_practice + workbench.test_review_type_display`：44 tests OK。
- 真实批次复验：`structure.json` 变为 `6 [] image_only`、`8 [1,2]`、`22 [1..6]`、`29 [1..6]`、`44 [1..5]`、`72 [1..8]`、`78 [1..5]`、`84 [1..5]`、`102 [1,2,3]`（end 120）；`structure_gate --force` → 通过「27 单元（独立 22 / 组合 5）」；`preassemble` completed（27 units，`语音题`/`单词拼写`/`完成句子`）；`GET /api/batches` 的 rows 27，题型与子类（`单词拼写 · 汉语及首字母`）正确。
- `node --check lib/client.js` 通过。
- 卡点台账：`ISSUES/open/2026-11-11-课时末节答案区被并入最后大题窗口导致题号重复与缺选项.md`、`ISSUES/open/2026-11-11-审核页把大题标题当题型展示且子类缺失.md`。

## 补正：题组图片、答案来源与重试输入绑定

- 用户进一步指出前两题为组题且图片不显示。确认单元和预览切片均完整；`media_audit` 却按首个印刷题号把 `image2.jpeg/image3.jpeg` 判为页眉。现以已验证 scoped 窗口归属优先于页眉/宽高比启发式；显式排除仍优先，过期 stem、未知边界依据、重复 UID、跨 owner 和 standalone 过宽窗口均不能提升。
- `server.detail` 补齐 `source_section`、`printed_numbers`、数组 `subtypes`；`preview.batch_rows` 保留已有子类；详情明确组题/单独题，无印刷号不把内部别名显示成真实小题数。
- 新 `workbench.test_media_scoped_preview` 贯通 source_slice → source_blocks → detail → Handler JSON，并验证上述负例。最终关联回归 49 项通过、3 项既有跳过；真实 HTTP 两图返回 200/image/jpeg，分别 29747/28846 bytes。
- 原答案附录 9 段罗马大题有基础/提升重号。`upload_content` 按完整单课时骨架建立真实章节与行坐标；新增 `answers_direct` 和 `stage_preflight` 入口，只有完整且唯一的原文答案才经正常 export/import 交付，缺题/冲突拒绝，dry_run 与 scoped 请求不冒用整阶段交付。隔离副本 27 单元、41 项答案恢复 41/41，错误坐标与假缺失各拒绝 41/41，完整导入 model_calls=0；受影响回归 113 项通过。
- 知识点验收刷新派生 draft 后，旧重试沿用初始 prompt/hash 又调用模型约 172 秒才报 `context_changed`。`local_agent` 在每次 invoke 前核验初始精确绑定，变化立即结束；不放宽在途哈希、不自动复用旧提示词。新增回归先红（invoke 两次）、后绿（一次）；相关 4 项通过。首轮完整 local_agent 模块 40 项：36 通过、3 跳过、1 失败（候选缓存 invoke 次数）；已用同环境 HEAD `061b5518a51361ab0ac496b1a7d7d483bef7983d` 复现，该测试漏标生产与文档已明确的 POSIX 缓存前提。现原缓存命中测试限定 POSIX，新增 Windows 测试确认 cache=True 仍正常调用、不读写 POSIX 缓存，输入变更立即 context_changed、不再 invoke。最终模块 **41 项：37 通过、4 跳过、0 失败/错误**（0.365s）；全部 invoke 模拟，未调用真实模型。
- 全量隔离快照已执行但未全绿：workflow 2397 次结果事件为 2305 pass / 20 fail / 38 error / 34 skip；Node 254 项为 236 pass / 17 fail / 1 skip。包含 Windows 路径、python3 空壳、O_DIRECTORY、空 profile 与测试隔离设置问题；未经同环境 HEAD 对照，不宣称均为旧基线。新增路径以定向回归和真实消费者复验为验收依据，保留完整失败报告。
- 真实答案首次复验在 `export_task` 等全局锁 5 秒超时：同一时间另一个 metadata 测试进程启动结构分类 router，占用共享模型槽。未杀未知进程、未绕锁；此为执行准入冲突，需释放后重新验收，不等于答案来源再次失败。

## 最终定向与真实交付复验

- 最终定向 `test_sync_practice/test_structure_gate/test_question_identity/test_answers_direct/test_scoped_lesson_answers/test_roman_answer_appendix`、媒体与题型 workbench 回归、changelog_policy 合计 **76 tests OK**（0.658s）。`node --check lib/client.js`、`node --check lib/index.js` 与 `git diff --check` 均通过。
- 2026-11-11 15:11，真实批次 `runner run --stage answers --force` 返回 exit 0：`status=completed/completed=true/deterministic=true/model_calls=0`；正常协议任务 `0dea0b48-4480-43…` 导入交付成功。随后知识点续跑被当前 preassemble/types 交付阻挡；监督器已于 15:11:31 启动 preassemble 刷新，不重复启动或杀掉其在途 worker。答案来源修复已复验，不把剩余流程写成完成。

## 最终边界审查补正

- 发现紧凑答案遇到不明非空续行会截断最后一题；现使整块拒收，完整匹配的「基础巩固/能力提升」横幅才可结束块，任意图片、伪横幅附加文字、重复题号继续拒绝。覆盖 `Unit\nTest` 及真实附录横幅；三个关联模块 **29 tests OK**。
- scoped 图片提升增加当前大题 `start/end/title/context/occurrence` 校验，来源 span 必须完全位于唯一当前大题内；不能仅凭 stem 在宽窗内唯一出现就提升邻题图片。
- 最终课时关联回归 **79 tests OK**（0.789s）；正式答案只读复验 **41/41** 通过当前来源校验，确定性重建与已接受答案逐项相同、缺失 0。真实 HTTP 两单元均为 group，题型语音题、子类数组；图片 image2/image3 均保留，分别 200/image/jpeg、29747/28846 bytes。
- 回顾经验已按 `proactive-self-improving-agent` 记录到 `.learnings/LEARNINGS.md`（LRN-20261111-002），覆盖答案续行与当前来源边界的双向回归。

## 卡点留档与接续状态

- 已复验的结构、题型显示、题图误隐藏、罗马答案绑定和旧输入重试五项台账均通过 `issue_log fix` 移入 `ISSUES/fixed/`，修复提交 `8e4b470`。
- 15:23:50 知识点阶段出现另一项真实阻断 `TEXTBOOK_PLACEMENT_EVIDENCE_MISSING`：`textbook placement requires nonempty evidence`（exit2/integration），已登记 `ISSUES/open/2026-11-11-课时知识点教材位置缺少非空来源证据.md`。实际候选不是缺键，而是把 `textbook.evidence` 写成单个字符串；任务伪 schema 未声明数组，知识点示例还残留答案/解析旧字段。同步任务规则、示例、实际构建提示回归和两份正文文档，保留非空字符串数组校验，不自动包装真实候选，也不把当前批次标记完成。
- 教材契约定向复验：`workflow.test_agent_tasks` **58/58**、`workflow.test_textbook` **13/13**；主线程合并 changelog_policy 再跑 **76 tests OK**（4.756s）。实际 build_task 示例可通过原导入校验，字符串、空数组与空字符串项仍拒收；无教材索引时不示例教材位置。
- 旧正式知识点产物只读检查 **27/27** 因额外 `grade` 字段被当前教材校验拒收，不直接复用、不自动删键。插件运维于 **15:31:41** 对实际受影响的全部 27 个单元启动知识点修复；新 attempt `knowledge-f82x7uce/input.txt` 已核实包含修正后的数组规则。此范围实际覆盖本卷全部单元，不将其掩饰为少数题修复；结构、答案、题型与其它阶段不重跑。教材卡点尚待真实验收，台账保持 open。
- 15:35 发现第二项确定性缺陷：`knowledge-f82x7uce/response.json` 的 27 行原始教材位置均不含 `grade`，但 `validate_placement → resolve` 规范化后补入 `grade`，再次验收又以 unknown fields 拒绝自己的输出。已登记 `TEXTBOOK_NORMALIZATION_NOT_IDEMPOTENT`；允许目录派生 `grade` 且逐字检查其等于目录值，错误年级/未知字段/证据错误仍拒绝。补规范化幂等及正式知识点产物再次导入回归；教材、任务契约和 changelog 合计 **77 tests OK**（5.063s）。不改候选标签，不靠模型重试消化校验器错误。
- Git 推送遇到 TLS 握手失败，尚未验证远端接收。台账提交使用显式 `--only` 文件集合，避免共享暂存区带入并行元数据改动；设备路由配置保持本地。

## 回退

`git revert` 本次提交即可；`sync_practice.answer_key_start` 与 `same_line_numbers` 都是新增函数，删除调用点即回到旧口径（旧口径会导致课时卷再次在结构闸门被答案区阻断）。
