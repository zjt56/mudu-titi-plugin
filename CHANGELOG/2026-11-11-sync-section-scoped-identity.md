# 2026-11-11 同步练习课时跨大题重号：来源单元身份键纳入题号作用域

## 1. 背景 / 触发

用户反馈「录一个课时就卡一下，效率非常慢」。排查两个真实同步练习课时批次
（`02…第二课时Pronun`、`03…第三课时Sectio`）后定位到：不是题型识别不准，而是
**受控修复脚本自身被跨大题重号卡死**，导致自愈跑不动、阶段 `failed` 后抛给不在线的
AI 运维等待，于是每录一个课时停一次。

根因：同步练习/课时材料每个大写罗马数字大题（Ⅰ./Ⅱ./Ⅲ.…）题号都从 1 重新开始
（Ⅰ.1 与 Ⅱ.1 都是 `numbers=[1]`）。三处代码只按「题号」识别单元身份，把跨大题的
同号误判为重复：

- `legacy_format._source_units`：以 `tuple(numbers)` 为键建映射，第二次遇到 `(1,)`
  直接抛「来源单元题号重复」→ `repair_source_tables` / `repair_source_options`
  连预演都跑不动。
- `legacy_format._bound_unit`：只按题号把候选行绑定到确认/重建单元，跨大题同号会串。
- `self_heal_actions._unique_source_part`：在**全卷**按 `_strong_number==number`
  求「唯一」，跨大题同号必然 >1，抛「Option/source question number is not unique」。

## 2. 改了什么

- `app/workflow/legacy_format.py`：新增 `_identity_key(obj) = (section_id, tuple(numbers))`。
  `section_id` 由 `sync_practice` 依来源位置确定性生成（非印刷题号），候选行、确认单元、
  原文重建单元三侧都携带。`_source_units` 的去重键与 `_bound_unit` 的绑定键改用
  `_identity_key`；`_key`（纯题号）保留给印刷题号证明与 `changes` 输出，语义不变。
- `app/workflow/self_heal_actions.py`：`_unique_source_part` 增加 `unit` 参数，
  把「题号唯一」与 `question_parts` 的作用域限定到单元的 `source_span`（单元已证明
  的来源窗口），与硬规则 27 的「题号作用域 / unit_id」一致；无 span 时退回全卷口径，
  保持旧边界。
- 新增回归 `app/workflow/test_legacy_format.py::SectionScopedIdentityTest`（4 例）：
  同题号跨 section 身份键不同、无 section_id 回退纯题号、跨大题重排不再误判重复、
  **同一 section 内真实重复仍必须报错**。

## 3. 没改什么 / 边界

- 只放宽「跨大题同号」为不同身份；**同一 section 内同题号真实重复仍拒绝**（有测试钉住）。
- 不猜内容、不改答案/标签/题干/切题边界，不做整卷重跑。
- `_prepare_options` 遇到单元级真实不可修复（如选项含 `[IMG]` 图片、选项文本与来源
  不符）仍会拒绝——这是安全边界，本次未放宽；本次只修「跨大题同号」这一系统性命中。

## 4. 如何验证

- 真实批次 03/02：`self_heal_actions._prepare_tables` 由改动前抛「来源单元题号重复」
  变为 `passed=True`；`_prepare_options` 不再报题号不唯一，进入真实内容判定。
- `test_legacy_format`：17 例全过（含新增 4 例）。
- `test_legacy_format + test_self_heal_actions + test_self_heal + test_self_heal_gaps
  + test_self_heal_verification + test_operations`：167→171 例，失败数保持基线 2
  （均为环境性：`write_text(newline=)` 在 py3.9 不支持、缺 `workflow.test_new` 模块，
  与本改动无关，基线同样 2）。
- 卡点台账：`ISSUES/fixed/2026-11-11-同步练习课时跨大题重号被判来源单元题号重复…md`。

## 5. 回退方式

`git checkout app/workflow/legacy_format.py app/workflow/self_heal_actions.py` 即可；
`_identity_key` 在无 section_id 时行为与旧 `_key` 一致，回退是安全的。

## 6. 后续（本次未做）

用户痛点的另一半——「单题不可修复 / 单题知识点越界 → 整阶段 failed」应改为
**局部重生成、保留成功单元**（`_affected_units`/`scoped_regeneration` 已能定位到具体
unit）。这属于更大的自愈行为改动，另立任务处理，不在本次确定性修复范围内。
