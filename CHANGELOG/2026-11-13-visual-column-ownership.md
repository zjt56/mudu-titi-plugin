# 整页视觉栏目归属与无号实际题

日期：2026-11-13。类型：运行行为 + 提示词契约 + 数据结构（新增只读证据与归属字段）。

## 背景与触发

用户 2026-11-13 要求视觉理解模型辨别题目栏目归属（基础巩固、栏目提示、熟词生义、创新训练、拓展设问等），包括“有的题直接归属栏目、确实没有印刷题号”，不能因无号漏题、伪造题号或猜挂到最近题。此前只有横幅板块识别和嵌入图语义，缺完整页栏目归属证据。

## 改了什么

- `app/workflow/source_pages.py`（新增）：用当前原件的完整页图建立只读证据。Word 优先，经官方 `dsoffice` 或 `@deepseek-ai/libreoffice-kit` 公开 API 渲染；PDF 可用真实完整页预览；支持显式 command renderer。保存原件/正文/页图摘要、页序、尺寸、缺字体警告，`load_verified` 重验。嵌入小图不冒充整页。
- `app/workflow/visual_columns.py`（新增）：把完整页和带行号 Word 文字交给当前选定视觉模型，产出 `01_source/visual-columns.json`。每列保存 label、role（container/guidance/question/extension）、parent_id、真实正文起止、可选 heading_line、实际 printed_numbers、页面 bbox 和理由；首尾 quote 必须逐字对应，父子范围、题窗重叠、页图/原件/正文 sha 变化一律拒绝，空结果、模型不确定都保留 unavailable 而不猜。多页用无损并排总览（不缩小/省略页面），bbox 仍按原页坐标；当前模型先输出思考的，可用其声明的低思考档并给足输出预算。
- `app/workflow/structure_ai.py`、`content_structure.py`、`question_identity.py`、`structure_gate.py`、`question_content.py`：结构摘要、身份分配、闸门、下游来源复建统一消费当前已验证栏目证据。无标题栏目题按 column 窗口直接绑定；有号题补 `source_section.column_path`，不改已有身份。完整页已准备但栏目归属未完成时，结构闸门报 `COLUMN_OWNERSHIP_UNRESOLVED` 阻断，不再让数字题号覆盖冒充无号题完整。
- `app/workflow/content_media.py`：仅允许 `source_pages.load_verified` 白名单内的整页图作为 broker 媒体，并绑定页清单 summary。
- `app/workflow/manifest.py`、`runner.py`、`preview.py`：栏目证据与整页清单进阶段指纹；结构闸门在 CLI 与监督器两条入口都先生成视觉证据；无号视觉题的预览必须经同一证据复验。
- `upload_content.py`：唯一绑定按起始行+完整签名证明所属大题；持久化的段落 end 允许落在唯一派生窗口内（标题重推会把尾随裸图片/材料行并入），超出窗口仍拒绝。
- `app/references/sync-practice-rules.json`、`sync_practice` 相关文档：`☆拓展设问` 仅在视觉证明依附前题时并入，独立栏目题单独收录，不再一律并入前题。
- `backend/wf-vision.mjs`：`length` 先于空响应判断；错误带安全诊断（stop reason、内容块类型、白名单数值用量、耗时），不回显思考/工具参数/原始错误；`reasoning` 只接受目录声明支持的档位，拒绝 `off` 与非法钳制，显式配置优先且冲突报错。
- 规则与文档：`AGENTS.md` 硬规则 34、`CHANGELOG/RULES.md` 一行、`app/docs/visual-columns.md`（新增）、`sync-practice.md` 拓展设问说明。

## 没改什么 / 边界

- 不生成、改写任何题干/选项/答案/解析/文章大意；栏目名称不作为题型或组题依据，题型与 group/standalone 仍由结构模型按内容判定并在闸门复验原文。
- 无号实际题保留 `printed_numbers=[]`，内部别名仅供既有身份/答案绑定，上传范围仍经唯一投影证明后为 `null/null`；无号子题远端契约未核实处不放宽。
- 视觉归属不证明答案值、解析或缺失；答案仍需独立来源坐标证明。
- 视觉不可用/过期不静默退回纯数字猜配；真实审核差异、上传/发布边界不变。
- 历史已完成批次不批量改写；验证使用隔离临时副本，不修改已归档原件或批准绑定。

## 验证

- 集成 Python 回归（source_pages / visual_columns / content_media / visual_transport / content_structure / structure_ai / question_identity / upload_numbering / structure_gate / question_content / sync_practice / stage_semantics / structure_visual_binding / runner_auto / answer_scope_trailing_lines / upload_content / scoped_whole_word_answers / answer_sole_topic / answers_direct）：**378 项通过（4 跳过）**。
- 答案/extract 侧并发核对（extract / extract_sync_dialogue_blanks / question_identity / structure_ai）：**67 项通过**。
- `node --test backend/*.test.cjs`：**365 项，363 通过、2 跳过、0 失败**；`node --check lib/client.js lib/index.js` 通过。
- 真实样本：Word 原件渲染为完整页（office-kit 公开 API）；当前选定模型单页可返回栏目标题；复杂 schema 曾整段输出用于思考并达上限，已改为 `length` 明确诊断、低思考档并给足预算——真实端到端栏目结果未在本机重跑中取得完整 JSON，未据此宣称通过。
- 视觉离线回归覆盖：伪造坐标/quote/父子/重叠、页面或原件变化失效、guidance 不成题、无号真实题 gate→复建→`null/null`、前题不吞后续栏目题、同名无号独立 ID。

## 未决 / 回退

- 真实模型对复杂整页 JSON 的稳定性未在本机确证；`visual-columns.json` 为 unavailable 时结构闸门显式阻断，需修复调用后再跑，不得放宽。
- 回退：撤回本提交即可恢复旧行为；无需改动用户批次数据。渲染器或视觉失败都只产生明确诊断，不写正式题目。
