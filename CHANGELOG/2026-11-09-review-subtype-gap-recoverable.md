# 2026-11-09 审核标签：目录子类漏填改为可恢复，不再阻塞整轮

## 背景 / 触发

批次 `【木牍教育】17年安徽中考英语` 的两轮审核（labels 一审全量）停在
`Invalid review labels: 题目：主类「书面表达」必须填写目录子类`，两次尝试同错后被自愈按“同错保守登记”，整卷审核卡住。

只读排查确认：失败轮中第 96 题（书面表达）4 个 check 里 2 个把 `assessment.subtypes` 填成空数组 `[]`，
另 2 个正确给了 `["列中文要点"]`。目录里「书面表达」有合法子类（列中文要点/列英文要点/思维导图/图片/图文结合/表格/图表/论坛帖文/其他），
因此 `taxonomy.check_question_labels` 报“必须填写目录子类”。这是**审核模型漏填子类**的确定性格式缺项，
不是原卷缺子类，也不是审核结论差异。旧逻辑把该缺项与其它结构错误一起 `raise`，导致整个 target 失败、进而整轮无法完成。

## 改了什么

- `app/workbench/taxonomy.py`：新增 `subtypes_required_but_missing(main_type, subs)`，只在“目录存在合法子类但模型给了空列表”时返回真；非目录主类、无子类主类、非空或非法列表都不算该情形。
- `app/workflow/review.py::_round_checks`：
  - assessment 的 `subtypes` 允许为空列表（当属上述可恢复缺项）通过前置“type and subtypes”检查；非字符串列表仍失败。
  - 标签结构校验 `check_question_labels` 仍执行，但把“必须填写目录子类”这条从致命问题中排除；其它结构问题（子类不在目录、知识点路径非法、缺少子题）仍整轮失败。
  - 缺项不静默放过：空子类与原标签不同，仍由 `review_consensus.compare` 生成“子类：原值 → 空”的真实差异，进入人工队列。
- `app/workflow/review_rounds.py`：审核提示词新增“子类必填”硬指引，明确有目录子类的主类必须给出合法子类、不得留空；`PROMPT_VERSION` 提升到 `review-prompt-v12`，使旧任务不再复用旧提示词。
- 回归 `app/workflow/test_review_rounds.py`：新增空目录子类不再致命、非法子类仍失败、空子类作为差异上报原标签三个用例。

## 没改什么 / 边界

- 不放宽审核口径：真实标签差异（题型/子类/知识点）仍进人工队列，`review_gate` 不因此自动放行。
- 不伪造标签：空子类不会被补写成某个具体子类，只作为“未评/与原标签不同”留给人工。
- 非目录主类、错误子类值、非法子类容器、缺少子题等仍严格失败，本条仅覆盖“有子类却漏填”的单一可恢复情形。

## 如何验证

- 真实第 96 题空子类 check 重放：`_round_checks` 不再抛错；`subtypes_required_but_missing` 对书面表达空值返回真、有值/无子类/未知类型返回假。
- `workflow.test_review_rounds`（含 3 个新用例）、`test_review_consensus`、`test_review_evidence_scope`、`test_review_rounds_recovery` 共 136 项通过；含 `test_upload_content` 的关联集 147 项通过。
- 全量 `workflow` 结果见提交说明（沙箱内 `test_local_agent_ownership` 依赖 `ps` 会报环境错误，非本次改动）。

## 回退方式

`git revert` 本提交即可恢复旧的“空子类整轮失败”行为；无数据结构迁移。
