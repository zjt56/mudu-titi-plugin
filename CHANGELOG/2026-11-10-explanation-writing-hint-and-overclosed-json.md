# 2026-11-10 修复 20 年卷两个卡点：书面表达解析与知识点 JSON 多写花括号

## 背景

用户反馈 `【木牍教育】20年安徽中考英语` 两个卡点：

1. 解析与文章大意：`q-81：Content source/ownership check failed: EXPLANATION_SOURCE_UNVERIFIED`，逐字定位失败、严格校验拒绝。
2. 知识点与教材匹配：`Invalid final JSON response: Extra data: line 1 column 10274 (char 10273)`。

排查：

- q-81 是书面表达，原解析就是附录的【思路点拨】段落。模型用 `source_ref` 指向 `01_source/附录.txt` 第 115 行（含 `【思路点拨】` 标签）。`content_fragments._EXPL_LABEL` 只剥 `【解析/答案/分析/详解】`，**没剥 `【思路点拨】`**，导致解析文本残留标签、无法与 `writing_hint()` 匹配；并且严格模式只在非严格时才接受思路点拨，于是报未溯源。
- 知识点那次失败：模型返回的根 JSON 在结束处**多写了一个 `}`**（over-closed），`_parse_response` 只处理“少写花括号（补齐）”，不处理“多写（去掉）”，因此维持 `Extra data`。

## 改了什么

- `app/workflow/content_fragments.py`：`_EXPL_LABEL` 增加 `思路点拨`，source_ref 解析书面表达解析时一并剥离该标签。
- `app/workflow/upload_content.py`：严格校验也接受书面表达的【思路点拨】原文解析（与非严格一致），要求归一化后与 `writing_hint()` 完全一致，仍拒绝改写/半截文本。
- `app/workflow/local_agent.py`：`_parse_response` 非严格模式增加“去掉多余尾随 `}`/`]` 后能解析成完整对象才接受”的修复；严格模式（审核）不修补，行为不变。
- 回归：`test_upload_content.py` 新增书面表达附录思路点拨严格通过/改写拒绝；`test_local_agent.py` 新增多余尾随花括号修复且不当内容仍拒绝。

## 没改什么 / 边界

不放宽解析真正需溯源的要求：非思路点拨的改写、半截、跨材料仍拒绝；严格审核解析不修补（沿用既有契约）；不猜标签。

## 验证

- 用该批次已保存的失败候选**确定性复盘**（不调用模型）：
  - 知识点 `knowledge-j3k7sfga/final.txt` 经新解析器可解析为 34 个单元；
  - 解析 `round-02-part-0000` 的 q-81 经 source_ref 展开 + 严格校验现 `passed=True`。
- `workflow.test_upload_content`、`test_content_fragments`、`test_local_agent` 通过（仅 `test_plugin_router_uses_node_without_posix_shell` 因本机无 DSH profile 的 `cordis.patch.yml` 预先失败，与本次无关，已在无改动基线复现）。

## 回退方式

回退本提交即可恢复原实现（会重现两个卡点）。
