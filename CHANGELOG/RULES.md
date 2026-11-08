# RULES — 规则与强调台账

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
