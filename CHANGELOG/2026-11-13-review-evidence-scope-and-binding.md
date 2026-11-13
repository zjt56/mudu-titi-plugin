# 审核引证范围与抽样来源归属修正（2026-11-13）

## 背景/触发

批次 `七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun` 在 `review` 阶段两轮均已完成，
却整轮被判 `Every target needs verifiable source evidence` / `REVIEW_SAMPLE_SOURCE_UNVERIFIED`，
`review_gate` 无法交付。排查发现三处「校验口径过窄 / 投影不一致 / 可重复写入」缺陷，
都不是模型编造：

1. 审核引证只允许命中 `unit["stem"]`。组题（语音题等）的 stem 只有答案空位行，
   作答指令写在大题题干 `source_section.title`，共享材料在正文窗口里；模型引用这些
   真实原文时被误判为「没有可核验来源」。
2. 二审按小题抽中组题时，小题窗口的确定被 `review_child_source._section_end_matches`
   否掉：确认小节 end 与解析 end 之间只差一行板块横幅（`B 能力提升`）和空行，
   横幅不在容错白名单里。
3. 二审抽样证据归属只认「命中小题原文 / 命中小题材料」，命中同组大题题干（真实原文）
   不算归属，于是 status 从 proven 降到 uncertain。

## 改了什么

- `app/workflow/review.py`
  - 新增 `_unit_review_text(unit, bound_texts)`：审核引证范围 = 课时/大题题干 + 单元 stem +
    真实正文窗口。窗口用与其它消费者相同的删除表（`upload_content._body_line_maps` /
    `_project_unit_to_body_file`）把确认单元的清洗坐标投影回 `01_source/正文.txt` 行号；
    投影不可证明时退回标题 + stem，不猜坐标。
  - `_round_checks` 改用 `_unit_review_text(...)`，空文本报
    `Target has no confirmed source text; review remains unchecked`。
  - `_sample_source_binding` 的引证归属增加两类：`child-verbatim-binding`（引证与
    `prove_child` 已证明的小题原文完全一致）与 `section-rubric-verbatim`（引证逐字命中
    确认单元的小节标题/上下文，即同题组共享作答指令）。二者只做来源归属，不替代
    `prove_child` 对小题存在性的证明。
  - 新增 `_narrow_sampled_group_subquestions(...)`：二审按小题抽中组题时，把「列出了整组
    子题且子题知识点全为空」的确定性投影收窄到被抽中题号；出现真实非空子题知识点时
    原样保留，照旧由覆盖校验报错，不静默丢弃。
  - `reconcile_unchecked` 改为只读的 `_prune_stale_round_unchecked(rows, rounds)`：闸门不再
    改写成 `07_review/unchecked.json`。旧实现每次闸门都把 review 阶段的产物指纹改掉，
    监督器随即判 review 过期并重跑，形成 review→gate→invalidate 死循环。
- `app/workflow/review_child_source.py`：`_section_end_matches` 容错白名单加入
  `lesson_bands.canonical_label(...)` 命中的板块横幅。仍要求 `end == lesson_container_end_line`，
  真实内容行依旧拒绝。
- `app/workflow/review_rounds.py`：二审抽样提示词明确「按目标列出子题」「assessment.subquestions
  只列出这个 number 本身」。
- `app/workflow/runner.py::do_review_gate`：删掉写文件的 reconcile 步骤，只读复核交给
  `review.evaluate`。
- 回归：`app/workflow/test_review_evidence_scope.py` 新增
  `test_group_rubric_and_window_count_as_verifiable_source`
  （组题引证命中大题题干与正文窗口可通过；编造题干仍被拒）；
  `test_review_rounds.py` 的提示词断言同步。

## 没改什么 / 边界

- 未放宽「引证必须是真实原文」：编造引文仍在 `_evidence_source` / `_quote_matches` 处失败；
  另一单元、年份等替身引证依旧报错（`test_year_or_another_unit_cannot_stand_in_for_target`）。
- 未把审核差异当卡点：审核口径不变——一审/二审与原标签不一致仍进人工队列，
  `review_gate=needs_review + execution_completed + continue_unqualified` 照旧表示
  「审核已完成、差异待人工判断」，不算故障、不阻断用户手动上传。
- 未改题型/知识标签判定，未改上传边界，未改写任何批次正文或答案数据。
- 板块横幅容错仅限「截断在板块 边界」且已要求等于本课时容器末行；普通内容行仍拒绝。

## 如何验证

- `python -m unittest workflow.test_review_evidence_scope` → 6 tests OK。
- 审核相关 8 个套件（evidence_scope / child_source / rounds / consensus / resolve_e2e /
  local_reuse / paper_scope / read_scope）→ `Ran 209 tests`，仅 2 个 `test_review_read_scope`
  失败，且已用 `git worktree` 在 HEAD `c566ca5` 上复现为既有失败，与本改动无关。
- 真实批次：`review.evaluate` 从 2 条 `REVIEW_SAMPLE_SOURCE_UNVERIFIED` 降为 1 条真实差异
  （`sec-77b3032e9f48da36-u91` 组级知识点：labels 一轮 1 条、sample 一轮 2 条，理由写明
  `/iː/ vs /e/`），无阻断项；随后 `review_gate` 记录
  「审核已执行完成，1 项差异待人工审核（未放行，未上传）」，
  `07_review/manual-review-queue.json` 保留该 open 项，`format_gate` 通过，
  批次进入 `pending_confirmation`（等待用户手动上传确认）。

## 回退方式

`git revert` 本次提交即可；`review.py` 的 `_unit_review_text` 与两处引证归属彼此独立，
需要单独回退时按 hunk 还原。批次侧不改数据，回退后重跑 `review_gate` 会重新按旧口径判定。
