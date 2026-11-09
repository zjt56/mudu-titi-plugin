# 2026-11-09 答案阶段无限重试：提示词示例漏了必填 `missing`

## 1. 背景 / 触发

用户提问「最新的试卷任务为什么卡住了」。批次 `【木牍教育】17年安徽中考英语`
（`C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】17年安徽中考英语`）
14:42 就完成了 `extract / structure_gate / preassemble`，之后近 20 分钟一直停在
`answers`、`types` 两个阶段，`workflow/agent-runs/` 里堆了 30 多个尝试目录。

定位结论：不是网络、不是锁、也不是模型坏了，而是**答案阶段的提示词示例与校验器契约不一致**：

- 校验器 `app/workflow/agent_tasks.py::_validate`（answers 分支）要求
  `("answers", "evidence", "missing")` 三个必填键，且要求 `answers` 与 `evidence`
  题号完全一致、`answers ∪ missing` 覆盖全部已确认题号；
- 但提示词里的 `example_payloads["answers"]` 只给了 `answers / evidence / difficulty`，
  **没有 `missing`**（该字段 2026-11-08 才变成必填，示例没同步）。

于是模型每一轮都照抄示例 → 第一次校验必定报
`Invalid fields; allowed=[...], required=['answers', 'evidence', 'missing']`；
带反馈的重试把 `missing: {}` 补上了，但 `evidence` 多出一个题号（97 vs 96），
又报 `Answer/evidence keys must match and be disjoint from missing keys`；
两次重试用尽后阶段回到 pending，supervisor 退避后再来一轮，**永远从同一份缺字段的示例开始**。
每轮约 3 次模型调用（deepseek-official/deepseek-flash，单次 50–150s），所以看起来「卡住不动」。

## 2. 改了什么

1. `app/workflow/agent_tasks.py::_build_task_template`
   - `example_payloads["answers"]` 补上 `"missing": {}`，示例与必填契约一致。
2. `app/workflow/agent_tasks.py::_fields`
   - 报错除 `allowed/required` 外追加 `absent=[...]`、`unexpected=[...]`，
     让重试反馈直接点名缺了哪个键，不用模型猜。
3. `app/workflow/agent_tasks.py` 新增 `_numbers_preview()`，答案阶段两处集合校验的报错点名题号
   （最多 8 个，超出写「等N个」）：
   - `answers_without_evidence=…`、`evidence_without_answer=…`、`in_both_answers_and_missing=…`
   - `uncovered=…`、`not_in_batch=…`
   仍保留原报错前缀（`Invalid fields` / `Answer/evidence keys must match…` /
   `Every confirmed question…`），调用方与既有断言不受影响。
4. 回归测试 `app/workflow/test_agent_tasks.py`
   - 新增 `test_answers_task_example_carries_every_required_field`：直接解析提示词里的示例 JSON，
     断言 `answers / evidence / missing` 齐全且题号与 `missing` 不相交
     （**这条测试就是本卡点的守卫**：示例再漏字段会立刻失败）。
   - 新增 `test_answer_contract_errors_name_the_offending_fields_and_numbers`：
     断言报错确实点名 `absent` / `unexpected` / `evidence_without_answer` / `not_in_batch`。
5. **把这条经验写进代码规则**（用户 2026-11-09 追加要求：“修复也是bug并写入代码规则”）：
   - 生产侧自检：`app/workflow/agent_tasks.py` 新增模块级 `EXAMPLE_REQUIRED`（每阶段的必填键）+
     `_build_task_template` 在拼提示词前比对 `example_payloads`，缺键直接
     `ValueError(f"{stage} prompt example is missing required keys …")`——
     **在花模型调用之前就报错**，不再靠重试兜底。
   - 全阶段守卫测试：`test_every_stage_example_satisfies_its_declared_contract` 逐阶段
     （answers/types/explanations/knowledge/audio/assemble/review）构建任务、解析示例、
     断言必填键齐全，并断言 `EXAMPLE_REQUIRED` 与 `ARTIFACTS` 阶段集合一致
     （新增阶段漏登记也会失败）。
   - 文档规则：根 `AGENTS.md` 硬规则 19「提示词示例即契约（示例与校验器同源）」，
     并同步 `CHANGELOG/RULES.md` 台账一条。

## 3. 没改什么 / 边界

- **没有放宽校验**：题号覆盖、`answers`/`evidence` 一致性、答案必须逐字出现在原卷答案材料里、
  `missing` 必须带「未提供/无」这类缺失观察证据，全部原样保留。
- 没有改上传/发布边界，没有动任何批次数据产物；没有重跑或改写用户已归档批次。
- 没有改模型路由（仍是 `deepseek-official/deepseek-flash`），没有改 `max_input_chars`。
- 没有让流程「跳阶段」：`answers` 仍然必须真正通过校验才会进入 `types` 之后的链路。
- 顺带把若干历史记录的「## 改动 / ## 变更 / ## 修改」标题统一成制度要求的「## 改了什么」，
  并给 3 条记录补上中文验证结论（`workflow.test_changelog_policy` 在 HEAD 上本来就是红的）。
  这些只改标题/结论措辞，不改任何事实与数字。

## 4. 如何验证

修复前（同一批次、真实运行，15:02:35 那一轮）：

- 产物键：`['answers', 'evidence', 'difficulty']`（缺 `missing`）
- 校验报错：`Invalid fields; allowed=[...], required=['answers', 'evidence', 'missing']`
- 重试：`evidence` 97 项 vs `answers` 96 项 → `Answer/evidence keys must match and be disjoint from missing keys`

修复后（同一批次，15:05:45 起跑的真实一轮 `answers-123gy5tm`）：

- 提示词示例已带 `"missing": {}`（直接读 `agent_runs/answers-123gy5tm/input.txt` 确认）；
- 模型返回键：`['answers', 'evidence', 'missing', 'difficulty']`，
  `answers` 96 项、`evidence` 96 项、`missing` 0 项，题号一一对应。

**真实流水线已恢复推进（同一批次，未重跑、未改任何产物）**：

- 15:08:54 `run-log.jsonl`：`agent_protocol_import stage=answers status=completed`
  （task_id `0942539a-9a5f-4daf-8f6b-ce0cd70e7a49`）；
- 15:08:55 `agent_protocol_import stage=types status=completed`
  （task_id `29c3ffc8-a72e-4b05-b510-e1fb0443c537`）；
- 15:08:57 起 `app/audio/worker.py transcribe` 子进程已启动（听力转写，按硬规则 15 与后续内容阶段并行、不阻塞组装）；
- 修复前 14:42–15:05 近 20 分钟只有 `extract / structure_gate / preassemble` 三个 completed，其余全 pending。

测试（`app` 目录，隔离 TEMP，`PYTHONUTF8=1`）：

- `python -m unittest workflow.test_agent_tasks` → **48 tests OK**（含 2 条卡点守卫 + 1 条全阶段示例自检）
- `python -m unittest workflow.test_agent_roles workflow.test_no_ai_generation_rule workflow.test_task_template_cache workflow.test_agent_protocol workflow.test_wire_format` → **63 tests OK，1 skipped**
- `python -m unittest workflow.test_changelog_policy` → **5 tests OK**（规则台账/索引/记录章节齐全）
- 生产自检实测（注入探针，不改产品代码）：给 `EXAMPLE_REQUIRED["answers"]` 临时加一个不存在的键后构建任务，
  直接抛 `answers prompt example is missing required keys ['__probe__']; models copy the example verbatim, so it must track the validator contract`，
  即**不发模型调用就失败**；恢复后 48 项全通过。

## 5. 回退方式

`git checkout -- app/workflow/agent_tasks.py app/workflow/test_agent_tasks.py AGENTS.md CHANGELOG/RULES.md`。
回退后该批次会重新回到「答案阶段每轮缺 `missing` 必然失败」的循环，因此不建议回退。
