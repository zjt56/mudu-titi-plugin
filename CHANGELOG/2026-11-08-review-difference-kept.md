# 2026-11-08 更正：一审与原标签不一致必须进待审核

## 背景 / 触发

用户反馈：22 年卷第 40/72 题校验通过后，**一审与原标签明明不一致，却没有被标成待审核**。
用户明确规则：“有差异你就待审核，没有差异就默认正常。”

排查发现两处同源错误：都用“审核判断 ≠ 当前正式标签”来判定“过期结论”，
而这两者不同恰好就是**真实差异**本身。

1. `workflow/review.py` 的 `_obsolete_label_finding()` 在 `evaluate_rounds`
   里过滤 findings，把 22 年卷 14 条“一审 vs 原标签”的知识点差异全部丢掉，
   导致 `review_gate` 判为通过。
2. `workflow/ingest.py` 的 `_stale_review_finding()` 在建/重建人工队列时做同样的
   过滤，把 34 条真实差异移进 `retired-round-findings.json`。

另外，上一版把 `review.py`/`review_rounds.py` 源码哈希纳入审核绑定指纹，
导致**每改一次实现就把刚跑完的审核轮次判成过期**（队列随之清空）——这是设计错误。

## 改了什么

1. 删除 `review._obsolete_label_finding` 及其三处调用；`review_gate` 只在差异
   都被人工处理（resolved）后才放行。能力受限过滤（`_is_capability_gap`）保留。
2. 删除 `ingest._stale_review_finding`；只保留“标签已补齐后，‘原题缺失标签’类
   误报可以清理”的 `_stale_missing_label_finding`。
3. `review._review_rule_revision()` 改为**显式版本常量** `REVIEW_RULES_VERSION`
   + 角色提示词字节，不再对源码做哈希：实现修复不会作废已跑完的轮次，只有规则
   口径真正升级（手动递增）或提示词改动才要求重审。
4. `review._round_checks` 证据路径接受省略 `rules:` 前缀的绑定文件
   （如 `references/knowledge-catalog.json`），不再因前缀不一致把整轮判失败。
5. 回归：`test_first_round_label_difference_stays_a_manual_finding`、
   `test_rule_catalog_evidence_without_prefix_counts_as_bound`。

## 没改什么 / 边界

- 不放宽知识点判定：差异仍按“完整路径集合是否相同”比较，任一来源不同即进人工。
- 能力受限（图片/音频无法核验）仍只作 informational，不进队列、不阻断。
- 已 resolved 的人工决定继续有效；删除的只是“未处理却自动消失”的路径。

## 如何验证

- `workflow.test_review_rounds`（64 项）、`test_review_consensus`、
  `test_review_local_reuse`、`test_review_child_import` 全部通过。
- 22 年真实批次重建后：`manual-review-queue.json` 13 项（全部为知识点差异），
  `review_gate` 为 `needs_review`，不再误判通过；图片类 q-2 仍为 informational。

## 回退

如需回退，只恢复 `ingest._stale_review_finding` 与 `review._obsolete_label_finding`
及对应调用；不要恢复源码哈希绑定（会重新引入“改代码就清空审核队列”的问题）。
