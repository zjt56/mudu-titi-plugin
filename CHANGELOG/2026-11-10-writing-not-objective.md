# 2026-11-10 书面表达不再按客观题严格校验

## 背景

用户反馈：答案与解析阶段在「书面表达（作文）」卡住。书面表达没有确定答案，是作文；可能有范文与写作指导，也可能都没有。只要题目存在即可，不应按常规题目校验答案/解析，缺失也是正常的。

## 改了什么

1. 硬规则：`AGENTS.md` 新增第 23 条“书面表达不按客观题校验”。
2. `agent_tasks._validate`（answers 阶段）：
   - 书面表达题号不再要求出现在 `answers` 或 `missing`（缺失即可）；
   - 若提供答案/范文，不做逐字来源绑定；`missing` 证据不要求“无/未提供”字样。
3. `upload_content`（解析/大意来源审计，explanations 导入与 validate 体检共用）：
   - 书面表达的 `ORIGINAL_EXPLANATION_OMITTED` / `ORIGINAL_SUMMARY_OMITTED` 不再判错（缺失正常）；
   - `SUMMARY_SOURCE_UNVERIFIED` / `EXPLANATION_SOURCE_UNVERIFIED` / `EXPLANATION_CROSSES_MATERIAL` 对书面表达只记 **warning**，不阻断；
   - 通过 `unit_type`、`item.question_type/type`、确认结构题号三种方式识别书面表达。
4. `check_answers.py`：书面表达题号不参与答案覆盖/一致性核对。
5. `check_explanations.py`：书面表达不做逐字溯源审计。
6. `publish_check.py`：书面表达缺少 `difficulty` 不再阻断（A3 值仍校验）。
7. 提示词：
   - `content_rules.RULES`：书面表达无确定答案，范文/指导可有可无，缺失填「无」正常，禁止自写范文。
   - `RULES["answers"]`：不得要求或编造书面表达答案。
   - `roles/explanations.md`、`roles/answers.md` 增加书面表达例外。
   - `docs/content-labeling.md` 增加“书面表达例外”段落。
8. 测试：新增书面表达豁免用例（无答案/无解析通过、有范文不阻断、普通题仍严格）；原有涉及书面表达“仅提示不阻断”的用例改为断言 warning。

## 边界

- 只放宽书面表达；单选、完形、阅读、听力等客观题仍严格校验答案来源与解析绑定。
- 书面表达仍要求题目/题干存在；若原卷确有【参考范文】/【思路点拨】，仍逐字保留，无法逐字定位只提示。
- 禁止用 AI 生成标记或自写范文冒充原文。

## 验证

- `workflow.test_upload_content + test_agent_tasks`：106 项通过（含新增书面表达用例）。
- 全量工作流结果见后续补充。

## 回退

回退 `upload_content` 的书面表达分支、`agent_tasks` 答案覆盖与绑定豁免、`check_answers`/`check_explanations`/`publish_check` 豁免、提示词与 AGENTS 第 23 条即可。
