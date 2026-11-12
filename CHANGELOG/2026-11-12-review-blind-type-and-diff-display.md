# 一审盲判误判修复（无选项题不得判单项填空）＋待审核差异展示

## 背景 / 触发
用户反馈批次 `04七上人教版课时A计划StarterUnit1Hello!第四课时Projec`
「一审正常为什么也标了待审核」。

核查结论：
- 程序判定本身正确：一审（labels 轮）与本标签**不一致才进队列**；同批 Ⅱ 节 5 道真
  单项填空一审判对、原标签一致，全部**没进**队列。
- 18 项差异里 **12 项是一审判错**：Ⅰ 节 6 道「根据句意用所给词的适当形式填空」
  （原标签 单词拼写/适当形式）被一审判成 `单项填空/四个选项`，而这一节根本没有
  A/B/C/D 选项；u12 的一审理由甚至自写「按单项填空/四个选项记录不合理」却仍填了
  单项填空。原始模型输出（workflow/review-attempts）两次均如此。
- 另 6 项是知识点标签真实差异（模型与原标签判断不同），属于待审核应保留的产出。
- 展示层「一审看着正常」源于 `verdict=ok`（执行完成）与「标签一致」是两个口径，
  且列表行只显示数字徽标，看不到一审到底判了什么。

## 改了什么
`app/workflow/review_rounds.py`（一审/二审盲判提示词）
- 新增【无选项不判选项题型·必须遵守】：只有题干/材料确实存在 A–D 等并列选项才可
  判含选项题型；只有空档填词/作答短句、没有字母选项的题不得判单项填空，应按真实
  形态判单词拼写/完成句子/翻译/书面表达等。
- 删除原「『单项填空』有 四个选项 等」示例（模型易照抄该值）。
- 新增【主类与知识点维度自洽·必须遵守】：主类定了，知识点只能取该主类允许的维度
  根；单词拼写只允许 词汇/语法/语音，严禁 语用·主题；落不进允许维度时省略该维度，
  绝不写超范围路径（超范围会让整轮标签校验失败、本轮作废）。
- `app/workflow/test_review_rounds.py` 新增 `test_prompt_forbids_option_types_without_letter_options`
  （两轮提示词都含新规则、旧示例已移除、子类必填仍生效）。

`app/workbench/server.py` + `lib/client.js`（展示）
- `mark_row` 增加 `open_diffs` / `open_summary`：把「题型/子类/知识点：原标注=X；
  本轮判断=Y」直接挂到行上。
- 列表徽标由裸数字改为「待审 N」，悬停显示 `open_summary`；详情页「待审标签」同样
  带悬停差异说明。

## 没改什么 / 边界
- 没有放宽也不允许自动 resolve：仍坚持「一审/二审与原标签不一致就是差异，必须进
  待审核」，禁止为放行改判/改标签；一致就不进队列的行为保持不变。
- 没有为了消除差异而放宽标签校验；主类/子类/知识点维度校验保持原样。
- 只影响新一轮盲判与展示；历史审核记录与批次数据未改写（旧轮次归档在
  `workflow/review-history/`）。
- 前端为宿主插件 `lib/client.js`，需宿主重载/刷新生效。

## 如何验证
- `PYTHONPATH=. python3 -m unittest workflow.test_review_rounds workflow.test_label_review
  workflow.test_review_consensus workflow.test_review_rounds_recovery` → **173 tests OK**（skipped=3）。
- `node --check lib/client.js lib/index.js` 通过。
- 实批复跑：重跑一审后 `18 项待审核 → 6 项`，`type/subtypes` 的 12 项误判全部消失，
  剩余 6 项全部是 knowledge 真实差异；`review_gate` 报告「已执行完成，6 项差异待人工
  审核」，银川 `review` 轮次 complete。
- 复跑中出现并已修的连带问题：新一审判 u15 为 单词拼写 却给 `语用/情景交际`，触发
  维度校验失败使整轮 labels 作废；已由【主类与知识点维度自洽】规则约束。

## 回退方式
回退本提交即恢复旧提示词与旧展示；不影响已归档轮次与批次数据。

## 后续深查更正
2026-11-12 后续深查发现：第三次 labels 调用仍复用冻结旧提示，未包含后来添加的维度规则；不能把该次成功归因于维度规则。原始输出矛盾有证据，但“模型一定照抄示例”的因果没有证据。前端还存在完整路径被缩为叶名后比较、顺序敏感、轮次缺 differences 及缓存漏输入等确定性缺陷，故此前“程序判定本身正确”的表述不能扩大到整条展示链路。详见 `2026-11-12-review-status-root-cause-hardening.md`。

## 已知遗留（本次未改，改动前即失败）
`workflow.test_workbench_review_status::test_stale_round_keeps_history_without_claiming_current_completion`
在本次改动前就失败（`git stash` 后复现）：同一目录两次 `review_snapshot` 因
`_REVIEW_SNAPSHOT_CACHE` 命中首次结果，第二次 patch 的 evaluate 结果不被采用。
