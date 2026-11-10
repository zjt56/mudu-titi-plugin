# 2026-11-10 强化内容来源优先级：Word > PDF > 空占位

## 背景

用户明确要求：“无论是试卷还是答案解析，文章大意等，都以 Word 为准；如果 Word 没有，优先取 PDF；如果还是没有，就留空的占位符。”并要求把规则同时写进提示词和代码，避免再次出现来源绑定卡点。

## 改了什么

1. 硬规则：`AGENTS.md` 第 4 条补充“Word 缺失允许 PDF 兜底”，新增第 22 条“内容来源优先级（Word > PDF > 空占位）”。
2. 提示词：
   - `app/workflow/content_rules.py::RULES`（解析/大意任务）：明确 Word 优先；Word 缺失才在 `01_source/materials/attachment_*.txt`（PDF/OCR 提取文本）定位同一题/同一篇并逐字保留，evidence 注明 PDF；两者都无填「无」；禁止用 PDF 覆盖 Word、禁止生成改写与跨材料借用。
   - `app/workflow/agent_tasks.py::RULES["answers"]`：答案同样按 Word → PDF → 缺失记录，禁止猜答案或跨材料搬运。
   - `app/workflow/agent_tasks.py::RULES["assemble"]`：上游解析/大意若明确标注 PDF 兜底（Word 缺失），装配逐字保留，不因来源于 PDF 拒绝。
   - `app/prompts/roles/explanations.md`、`roles/answers.md`：职责与自检加入来源优先级。
   - `docs/content-labeling.md`：新增“来源优先级（硬规则）”段落。
3. 代码：
   - `app/workflow/explanations_direct.py`：新增 `_pdf_sources()`。Word 有该题解析/该篇文章大意时保持 Word；Word 缺失时用 `01_source/materials/attachment_*.txt` 的同一题/同一篇逐字补齐，并在 evidence 追加“PDF OCR 兜底：<文件>”；都缺失仍填「无」。绝不覆盖 Word 内容。
   - `app/workflow/upload_content.py`：维持并明确“权威来源（Word/附录）优先；只有 Word 对应题号/题组缺失时才保留附件（PDF/OCR）行”，与提示词口径一致。
4. 测试：
   - `test_explanations_direct.py` 新增：Word 缺 57/58 与文章大意时由 PDF 补齐、Word 已有 56 不被 PDF 覆盖；仅有 PDF 时也能生成。
   - `test_agent_roles.py` 新增：解析/答案提示词与角色正文必须显式包含 Word/PDF 优先级。
   - 既有 `test_upload_content` 的“Word 优先、附件兜底、副本不参与唯一性竞争”用例保持通过。

## 边界

- 有 Word 时始终以 Word 为准，PDF 不覆盖、不纠正、不改写 Word。
- PDF 只补 Word 缺失的同一题/同一篇；跨材料借用、编造、AI 生成仍拒绝。
- 两者都没有时解析/大意填「无」、答案记缺失并留证据，不再空着或猜。
- 不重写任何已有批次产物；提示词变化会让旧解析任务失效并重跑（预期）。

## 验证

- `workflow.test_agent_roles + test_agent_tasks + test_explanations_direct + test_upload_content`：119 项通过（含提示词预算用例）。
- `workflow.test_explanations_direct`：6 项通过（含 2 条 PDF 兜底回归）。
- 全量工作流结果见后续补充。

## 回退

回退上述提示词与 `explanations_direct` 的 PDF 兜底、AGENTS 第 4/22 条及对应测试；行为回到“只用 Word，缺失即占位”。
