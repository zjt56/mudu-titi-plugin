# RULES — 规则与强调台账

## 2026-11-09 模型列表/路由只能出现真正可调用的 provider（NO_ADAPTER 卡点修复）

- 触发：批次【木牍教育】18年安徽中考英语 前置组装失败，stderr 实为 `dsh: NO_ADAPTER: no adapter registered for provider "magpie"`，界面却显示「Agent subprocess failed…重跑一次通常即过」。
- 路由 profile 只复制 base profile 的 `llm-pi-ai` / `llm-deepseek` 条目；宿主独有的中转 provider（WorkBuddy `magpie`、账号目录）不在其中，选中必然失败。
- 模型列表不得列出路由 profile 不能服务的 provider；`wf-router` 调用前必须校验 provider 在生成的路由 profile 中，否则带可选列表直接报错，不得静默换模型，也不得把配置错误伪装成阶段产物不合格。
- 落地：`lib/index.js::servableHostModels`、`backend/wf-router.mjs::overlayProviders`；测试 `backend/wf-router-native.test.cjs`、`backend/model-list.test.cjs`；记录 `CHANGELOG/2026-11-09-router-provider-guard.md`。

## 2026-11-09 模型不固定：每台设备跟随自己的 Harness 模型（用户明确）

- 原话：“模型不是固定的，是随着不同设备自由选择他们harness的模型”。
- 仓库 `config/model-router.json` 的 `default` 保持为空，不再把任何厂商/中转写死为默认。
- 路由在 `default` 为空时读取本机 `profiles/<profile>/cordis.patch.yml` 的 `agent-default-model`（顺序：`DSH_PROFILE` → `desktop` → `wf-deepseek-fast` → `wf-deepseek`），跟随该设备 Harness 菜单当前模型。
- 插件设置里显式指定的 `default` / `stages` / `roles` 仍然严格优先；显式配置存在但不完整或损坏时必须报错，不得静默换成其他模型。
- 缓存与分片快照的模型身份也绑定该 profile 文件，换模型后不会复用旧结果。
- 落地：`backend/route_pick.py`、`backend/wf-router.mjs`、`lib/index.js`、`lib/client.js`、`app/workflow/local_agent.py::_part_model_binding`；根规则8。

## 2026-11-09 题型判断真正纳入前置组装（用户明确）

- 原话：“按照我的要求修改，题型放在组装流程中”；“在切题的过程中顺便判断了题型”。
- 前置组装同一阶段建立来源对象并完成目录题型、子类、词数和完整性判断；来源与题型共同验收后交付下游，不能只合并流程图名称。
- 后续答案、听力和知识点可并行，解析等待答案；听力仅阻塞内容审核，不阻塞汇总和本地体检。
- `types` 保留为无模型的兼容交接校验和显式旧任务/局部修正身份，常规新批次不额外判型；局部修正须同步共享交付摘要，避免假过期。
- 落地：根规则20；`workflow/preassembly.py`、`contracts.py`、`agent_tasks.py`、`local_agent.py`、`agent_protocol.py`、`handoff.py`、`manifest.py`；回归 `workflow.test_preassembly`。

## 2026-11-09 快速纠错：同证据不得原样重试，代码缺陷走隔离修复（用户授权「可以」）

- 用户原话要点：要实现快速纠正卡点；授权按建议补齐故障证据包、重复失败升级、局部重生成、隔离代码修复/测试/受控更新/续跑。
- 卡点处理必须指出具体失败检查（报告、错误码、检查名、题号），不能只写"校验不通过"；`diagnose` 返回自包含证据包。
- 同一修复/续跑策略在**相同输入、证据与插件代码**上重复失败时，后端必须拒绝原样重试并升级为代码修复；输入或代码真实变化后才允许再试。限流、网络、超时等瞬时错误不在此列。
- 未知程序缺陷不得靠反复重试冒充修复：只能在隔离工作区复现、修复、跑测试、生成安装计划，再由受控宿主确认安装并单次重启；安装成功不等于故障恢复，必须重启后重新认领并用原失败检查与消费者链复验。
- 局部重生成只缩小重跑范围：仅内容阶段（答案/解析/题型/知识点/听力），只带本批次确认题号；未指定题号时只用证据中真实出现的单元，定位不到就拒绝，不得整卷重跑；上传/发布边界与确定性阶段永不适用。
- 隔离工作区不是操作系统沙箱；生产安装必须显式确认且宿主设置 `MD_OPERATIONS_INSTALL=1`，默认不可用。`lib/*.js`、配置、数据结构只产出补丁，不得宣称自动生效。
- 不自动上传、不发布、不改归档批次；真实卷只在明确授权下执行。
- 落地：`app/workflow/operations.py`、`app/workflow/operations_development.py`、`lib/operations-agent.js`、`app/docs/harness-operations.md`；测试 `workflow.test_operations`、`workflow.test_operations_development`、`backend/operations-agent.test.cjs`。

## 2026-11-09 共享导语归属修正（审核后修复）

- 原文坐标必须以已验证绑定的 line_basis 为准，不混用原始/去噪行号。
- 仅继承同节标题之后、首篇材料之前的完整公共导语；限定题号的指令不传播，本组专属要求优先。
- 公共导语缺失须由原文审计检出，预览和上传载荷同样保留。修正此前“标题之前”的错误表述。
- 落地：`shared_rubric.py`、`question_draft.py`、`question_content.py`、`preview.py`；根规则14。

## 2026-11-09 共用大题题干不得只挂在第一组（用户强调）

- 原话：“完型填空的AB，以及阅读理解的几篇文章都是共用一个题干内容的，请完善对应的部分内容，确保这部分的题干不丢失。”
- 完形 A/B、阅读各篇共用同一大题的作答导语；后一组的来源窗口只含自己的文章，必须补回同一大题标题之前的原文导语，留空视为丢失。
- 补入的导语只写 `stem`，不得据此截断材料；`A`/`B` 分段标签和文章正文必须原样保留。原文没印导语的试卷保持为空，不生成文字。
- `第一节`/`第二节` 是分段标记：识别时去前缀匹配，原文行按原样保存。
- 落地：根 AGENTS.md 硬规则14附注；`app/workflow/question_draft.py::_section_rubric`、`question_content.py::SECTION_LEAD`；`app/docs/workflow-regressions.md`；回归 `workflow.test_question_draft`；详见 `CHANGELOG/2026-11-09-shared-section-rubric.md`。

## 2026-11-08 Harness 运维接手必须保留现有功能（用户明确）

- 原话：“可以，你帮我去做一个这样的一个功能，前提不修改现有的一个已有的功能，你帮我完善这个。”
- 新增Harness运维会话为附加层；原录题、自愈、审核及上传入口保留，关闭或不可用不替换旧执行器。只有真实失败才升级，不能把标签差异或正常长时执行当故障。
- 处理记录必须区分已接手、已执行动作、待续跑、验证成功及需深入修复；只有当前产物经真实检查才可认定恢复。未知请求先查回执，不重放；写入绑定批次身份、接手代次和执行锁，不自动上传发布。
- 落地：AGENTS硬规则18；`app/workflow/operations.py`、`lib/operations-agent.js`、`lib/operations-settings.js`、原页面附加运维区域；`app/docs/harness-operations.md`。

## 2026-11-08 表格空列与格式诊断规则固化（用户强调）

- 原话：“将这个修复的规则写到插件里，避免下次出现此类问题。同时提交到GitHub。”
- Word 表格必须保留原件 `grid_before/grid_after` 明确记录的行首/行尾空白位置，转换为空单元格，不为凑行宽猜改 `rowspan/colspan`。存量恢复须有原件摘要匹配、唯一来源绑定及旧投影逐项相等的证据，经事务备份、来源审计与格式复验后安装。
- 格式诊断以结构化 `errors` 为本地失败依据，显示错误码与题组；`deferred/warnings` 不得误当阻断。报告缺损才回退日志的结构化错误，知识点目录快照/媒体部署等远端准备校验仍保留。
- 落地：根 AGENTS.md 硬规则16/17；`app/workflow/question_draft.py::_source_table_rows`、`self_heal_actions.py::_prepare_tables`、`diagnose.py::_format_detail`；`app/docs/workflow-regressions.md`；回归 `test_question_draft/test_self_heal_actions/test_diagnose`；详见 `CHANGELOG/2026-11-08-format-table-grid-diagnosis.md`。

## 2026-11-08 能力提示不得吞掉真实标签差异（2015 卷第 6–10 题）

- 用户纠正：一审与原标签不同却没有待审核标记，第 6 题及后续多题均如此。
- 程序按 type/subtypes/knowledge 的结构化值比较出的真实差异必须进队列；不能因审核者理由提及图片未看、音频未听、占位等，把该差异降为 informational。能力受限豁免只针对限制本身。
- 验证必须对照整卷全部比较结果、持久队列与实际工作台待审状态，不能只验证少量正常题或只报队列条数。
- 落地：根 AGENTS.md 硬规则 6；app/workflow/review.py::_is_capability_gap；test_review_consensus；CHANGELOG/2026-11-08-review-capability-keeps-differences.md。

## 2026-11-08 盲审隐藏标签不得误报缺失（2015 卷）

- 用户反馈：“一审和原标签都是一致的，但还是被打上待审核标签”，要求找出原因并根除。
- 盲审不公开原标签，模型只独立填 assessment，差异比较由程序负责；隐藏字段缺失不能成为人工发现。此类无依据结论由程序拒收并自动补审，原始响应保留，不改写成 ok。
- 标签一致但有真实答案/解析/原文问题仍须送审；真实知识点/题型/子类差异始终保留。修复后须重建已有派生队列，避免仅改提示而界面仍读旧误报。
- 落地：根 AGENTS.md 硬规则 6；app/workflow/review_rounds.py::_round_prompt（review-prompt-v10）；app/workflow/review.py::_reject_blind_label_absence；app/docs/review.md；CHANGELOG/2026-11-08-blind-review-label-absence.md。

> 用户强调过、反复强调过、以及新定的硬规则，全部记录在此。新增/修订规则时追加一行，并同步 `AGENTS.md` 第二节。
> 「原话要点」尽量保留用户当时的表达，避免转述失真。

| 日期 | 规则要点（含原话关键句） | 落地位置 |
|---|---|---|
| 2026-11-06 | **零人工本地执行**：全程不得人工干预/判断/选择，不得因待复核、待核验、人工确认停住。 | `app/workflow/auto.py` ZERO_TOUCH；本 AGENTS 硬规则 1 |
| 2026-11-07 | **卡点必须真自愈**：AI 自己诊断原因、实际执行修复、复验和续跑；不能只加修复函数或提示、仅原样重试后停住，也不能等用户反复点继续。 | `app/workflow/self_heal.py`；硬规则 2 |
| 2026-11-07 起反复强调 | **任何单点失败都不能让整个项目停住**（答案提取、装配、任一步都一样）；出问题要自己再看一遍、修一下，不能就这样停了。 | `self_heal.UPSTREAM`、`defer_and_continue`、`supervisor`；硬规则 2 |
| 2026-11-07 | **子 Agent 各管一块 + 自带脑子检查**；角色化提示词与按角色独立选模型。 | `app/prompts/roles/`、`config/model-router.json` `roles`、`backend/route_pick.py` |
| 2026-11-07 | **排队区进度按可见流程图节点显示**，不出现内部 18 阶段导致的"十几/18"。 | `lib/client.js` `compactProgress`；硬规则 7 |
| 2026-11-07 | **重大改动与规则强调都必须落记录**：不要说反复叫去做，有重大变更也要记下来。 | 本 `CHANGELOG/`；硬规则第十条附注 |
| 2026-11-07 | **上游交付必须满足所有下游契约**："上游的东西必须符合下游的东西，否则上游的就不会让下游去推进流程；不要上个流程没完成，下个流程就启动"。未完成、字段/格式/覆盖不合格、输入或绑定过期均不得视为可交付；无关分支仍可继续。 | `workflow/contracts.py`、`handoff.py`、`manifest.py`、`agent_protocol.py`、`auto.py`；硬规则 2 |
| 既有 | **不自动上传/发布**：本地运行到 format_gate 为止，远端提交须明确授权。 | `auto.py` ZERO_TOUCH；硬规则 3 |
| 既有 | **原文保真**：题干/选项/材料须等于原卷；Word 与 PDF 并存以 Word 为准。 | `question_draft.py`、`assemble`；硬规则 4 |
| 既有 | **空白语义**：未判定留空；确认不存在保留占位，不得一律清空。 | `content_rules`；硬规则 5 |
| 既有 | **审核只收两类差异**（知识点/基本差异）；难度、来源、分类、选题、词数不进审核；能力受限只作提示。 | `workflow/review.py` MANUAL_REVIEW_SCOPE；硬规则 6 |
| 既有 | **模型不静默兜底**：配置缺失/损坏/缺默认必须报错。 | `backend/route_pick.py`；硬规则 8 |
| 既有 | **质量≠执行**：已执行有差异不等于通过，`release_ready` 须真实审核。 | `manifest`；硬规则 9 |
| 2026-11-07 | **本次重构范围强调**：之前已添加的规则不要修改，不让用户重复处理；现有工作流、功能、界面保留，只做插件底层重构与优化。本条记录实施边界，不修订既有业务硬规则。 | [底层重构记录](2026-11-07-runtime-core-refactor.md)；规则与界面 SHA-256 保护、局部恢复与原故障复验回归 |
| 2026-11-08 | **过期/未完成审核不阻碍上传**：批次产物“过期/内容变化”与“未完成审核/有审核差异”都不阻碍上传到题库服务器；只要本地体检（validate）曾通过即可上传，内容正确性由用户自己把关（标签/结构等硬校验仍保留）。“这个过期状态不阻碍批次上传”“未完成审核也不影响批次上传到题库服务器”。 | `workbench/server.py::_check_upload_preconditions`；根 `AGENTS.md` 硬规则 3 |
| 2026-11-08 | **哈希不得成为序列化噪声卡点**：工作流不得因 JSON 空白、键顺序或时间戳差异反复失效/重跑；模型限流不得冻结不依赖模型的本地阶段或独立分支。精确哈希保留给在途任务输入绑定、原件/媒体证明、音频一致性及上传冻结，不得用放宽安全边界制造假完成。 | `workflow/manifest.py` 规范化阶段产物签名；`workflow/stage_semantics.py`；`workflow/auto.py` 冷却隔离；根 `AGENTS.md` 硬规则 2；`CHANGELOG/2026-11-08-normalized-stage-fingerprints.md` |

## 2026-11-08 卡点反馈必须标明具体批次（用户强调）

- 原话要点：2014 年试卷本来已经跑完，但错误报告没有精准指到具体试卷，反馈后又把 2014 批次拉出来重跑；后续卡点必须指出具体批次。
- 落地：`app/workflow/diagnose.py` 增加 `batch_name`，诊断摘要带批次名；`lib/client.js` 卡点条显示批次名，继续处理失败提示带批次名；根 `AGENTS.md` 硬规则 11。
- 边界：只改善错误定位和反馈，不改变自动推进、重试、自愈或阶段规则；泛化反馈不得猜测目标批次。

## 2026-11-08 听力题不标语音知识点（用户强调）

- 原话要点：听力关键词语选择题每道都冒出「单字母在单词中的语音变形」，
  但听力题不该有这个考点，语音应该是语音题的考点。
- 落地：`app/references/type-knowledge-map.json` 的「听力关键词语选择」去掉 `语音` 维度，
  只留 `词汇 + 答案考点`；`app/skills-reference/type-knowledge-map.md` 同步。
- 边界：`语音题` 仍标语音；其它题型未动。
| 2026-11-08 | **重复来源不参与唯一性竞争**：同一份卷被重复提取的 PDF/OCR 附件副本（`material-roles.json` 中 `content_authority=false`）只作佐证，不得让逐字解析被判“来源不唯一”；权威原文内部同题两份不同解析仍算冲突。**卡点说明必须给出具体检查项与判定口径**，不能只写“按阻断项修复”。 | `workflow/upload_content.py` `reference_only_sources`；`workflow/diagnose.py` `CHECK_HINTS`；根 `AGENTS.md` 硬规则 2/4；`CHANGELOG/2026-11-08-explanation-reference-authority.md` |
| 2026-11-08 | **有差异就必须待审核**：一审/二审与原标签的知识点/题型不一致即真实差异，必须进人工队列；不得用“审核判断≠当前正式标签”判过期而静默丢弃（用户原话：有差异就待审核，没差异默认正常）。图片/音频能力受限仍只作 informational。 | `workflow/review.py` 移除 `_obsolete_label_finding`；`workflow/ingest.py` 只保留“标签已补齐”清理；根 `AGENTS.md` 硬规则 6；`CHANGELOG/2026-11-08-review-difference-kept.md` |

## 2026-11-08 原卷预览图片显示规则（用户强调）

- 原话要点：表格里图片要和那段文字在同一个单元格里，不要分开；表格要自适应宽度、自动断行，不要左右滑动；原图（尤其表格内图）要缩得非常小。
- 落地：
  - `app/workbench/server.py::source_blocks` 按单元格切分，`cellImages[行][列]=[图片名]`，图片留在原地；`attach_media` 为 `cellImages` 补 URL。
  - `lib/client.js` 表格 `width:100%; table-layout:fixed`，单元格 `overflow-wrap:anywhere; word-break:break-word; white-space:normal`；`.wf-table-imgcell` 不再 `nowrap`；表格外容器去掉 `overflow-x:auto`；正文图 `width:100%; max-width:200px`；表格内缩略图极小（约 36×28）。
  - 图片能力受限仍只作 informational，不进人工、不阻断。
- 关联记录：`CHANGELOG/2026-11-08-table-image-in-cell.md`、`2026-11-08-table-responsive-wrap.md`、`2026-11-08-preview-table-image-and-size.md`。

## 2026-11-08 题组材料必须在题号前完整绑定（用户强调）

- 原话要点：把这类题目产生的问题写进修复规则，避免下次再出现（2024 卷 group-56-57-58 阅读理解因表格材料丢失报 A8「题组 unit_kind=group 但 material 为空」）。
- 根因：`question_draft._bound_source` 只对 `span.basis==ai_classified` 走整窗绑定；本卷 basis 为 `cleaned_source`，退回按 stem 唯一匹配定位。stem 只含题干（从首个题号起），定位把题号前的文章/表格材料切掉，group 的 material 变空。
- 规则：group（题组）绑定来源时必须使用**完整 confirmed span 窗口（材料 + 题目）**，定位 stem 只能证明其在窗内，不得据此缩短范围、丢弃题号前的材料（文章/表格/导语）。`source_span.basis` 为 `ai_classified` 或 `cleaned_source` 均属可信边界。standalone 仍只绑定自身题干。
- 落地：`app/workflow/question_draft.py::_bound_source` 增加 group span 整窗分支；`CHANGELOG/2026-11-08-group-material-table-span.md`。
- 边界：只修 group 材料绑定；不改题目内容/答案/标签；表格材料用 `[TABLE ...]` 原样保留，A8 要求 group 有 material + subquestions。
| 2026-11-08 | **格式小问题不阻断流程**：用户原话“标点符号呀、下划线呀，这些在流程通过性上都是小问题，只要不是大错误就行”；只有题目/题干/题号对不上才是大问题。据此把下划线/答题线占位、全角空档、`<br>`、首字母间距、表格列数/空档写法等降为 `[W…` 提示，不阻断 validate。 | `app/scripts/_common.py::report`（软提示不阻断）；`scripts/06-upload/check_underline.py`、`check_tables.py`、`check_text.py`、`audit_sentence_blanks.py`；`workflow/checks.py` blocking=False；根 `AGENTS.md` 硬规则 9；`CHANGELOG/2026-11-08-format-hints-not-blocking.md` |
| 2026-11-08 | **本规则写入程序**：上述“格式小问题不阻断”不是只在文档里说明，而是落在检查器代码——`app/scripts/_common.py::report` 的软提示机制、`check_underline`/`check_tables`/`check_text`/`audit_sentence_blanks` 的 `[W…` 前缀、`workflow/checks.py` 的 `blocking=False`。用户要求：“写到程序的规则里面”。 | 程序落地位置同上；`CHANGELOG/2026-11-08-format-hints-not-blocking.md` |
| 2026-11-08 | **预览必须完整展示题组材料**：用户反馈 2024 卷第一篇阅读“正文没了只剩选项”，实质是预览切片从首个题号开始截取、把题号前的 `[TABLE 2]` 海报材料丢掉（批次数据本身完整）。规则：预览片段必须包含题号前的全部共享材料且顺序在题目之前，不能因段落标签 A/B 或`※Notes：1.…2.…` 弱题号截断表格。 | `app/workflow/preview.py::slice_range`；`workflow/test_source_structure.py` 回归；根 `AGENTS.md` 硬规则 14；`CHANGELOG/2026-11-08-preview-keeps-group-material.md` |
| 2026-11-08 | **数据完整≠展示完整**：用户指出 2024 卷首篇阅读正文丢失未被检查出来。规定“内容丢失”类修复必须逐层验证（批次字段 → 预览切片 `slice_range` → `source_blocks` → payload），只验证一层不算通过；并必须补该链路回归测试。 | `app/workflow/test_source_structure.py` 新增切片回归；根 `AGENTS.md` 硬规则 14；`CHANGELOG/2026-11-08-preview-keeps-group-material.md` |
| 2026-11-08 | **听力并行、仅内容审核等听力**：用户要求听力耗时长，不要卡住后续；答案/题型/解析/知识点/组装及 meta/underlines/validate 都可与听力并行推进，只有内容审核（review）必须等听力跑完（审核要读听力全文转写核对语义）。| `workflow/contracts.py`：assemble.depends_on 移除 audio，review.depends_on 加 audio；`test_runner_auto` 用例更新；根 `AGENTS.md` 硬规则 15；`CHANGELOG/2026-11-08-audio-runs-parallel.md` |
| 2026-11-09 | **版本控制只在 master 一条线**：用户明确“不要加分支，直接在 master 上提交后续”。不新开 `develop`/`feature`/`hotfix` 分支，后续改动直接提交到 `master` 并推 `origin`；动手前先 `git fetch` 确认远端未领先，禁止 `push --force`。 | 本地与远端分支均只有 `master`（`git branch -a`）；根 `AGENTS.md` 五、协作提示末条；`CHANGELOG/2026-11-09-console-encoding-stage-wedge.md` 后续提交即按此执行 |
| 2026-11-09 | **附件 placement 只能是题库四值**：用户反馈题库「上传暂存 → 逐题确认」只报 `invalid_request`，根因是材料图片附件带 `placement=material`；题库只接受 `question/option/answer/explanation`，确认阶段整批 `400 invalid_attachment`。规则：生成线上载荷时 `material`/`stem` 必须归一为 `question`；任何新位置先确认远端确实渲染，不能把本地/装配内部位置直接上线。 | `app/workflow/wire_format.py`、`app/workflow/upload_payload.py`；回归 `workflow.test_upload_payload`、`workflow.test_wire_format`；`app/docs/upload-api.md`；`CHANGELOG/2026-11-09-upload-attachment-placement.md` |
| 2026-11-09 | **题目 ID 全局唯一、跨卷必须加前缀**：用户反馈 2014 卷确认后再确认 2015 卷报 `question_id_exists`。根因是本地 `q-N`/`group-N` 只在单卷内唯一，题库题目 ID 是全局主键；云端拒绝重复是正确行为。规则：上传冻结时对本地形态 ID 自动加 `<年份>-<批次哈希10位>-` 前缀，显式 ID 保持原样；同批次重传相同前缀属有意防重，替换旧卷先删旧草稿或另建批次。 | `app/workflow/upload.py::_auto_id_prefix`、`app/workflow/upload_payload.py::build_payload(id_prefix=...)`；回归 `workflow.test_upload`、`workflow.test_upload_payload`；`app/docs/upload-api.md`；`CHANGELOG/2026-11-09-question-id-paper-prefix.md` |
| 2026-11-09 | **提示词示例即契约（示例与校验器同源）**：用户要求“修复也是bug并写入代码规则”。根因是 `answers` 的示例漏了后来才设为必填的 `missing`，模型每轮照抄示例 → 首轮校验必失败 → 补字段的重试又出现 `evidence` 多一个题号 → 两次重试用尽回到 pending，再从同一份坏示例重来，阶段无限空转。规则：示例必须含校验器全部必填键，必填键变更须在同一次改动内同步示例/规则/回归；构建任务时 `EXAMPLE_REQUIRED` 自检缺键直接报错；校验报错要点名缺/多的键与不匹配题号；确定性失败不得靠重试消化，同一阶段连续多轮同一报错即按硬规则 2 升级修复。 | `app/workflow/agent_tasks.py::EXAMPLE_REQUIRED`、`_build_task_template`、`_fields`、`_numbers_preview`；回归 `app/workflow/test_agent_tasks.py::test_every_stage_example_satisfies_its_declared_contract`、`::test_answers_task_example_carries_every_required_field`；根 `AGENTS.md` 硬规则 19；`CHANGELOG/2026-11-09-answers-example-missing-field.md` |
