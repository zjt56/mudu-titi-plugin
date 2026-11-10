# 2026-11-10 彻底修复解析溯源与 JSON 结构类卡点，并强化提示词

## 背景

用户要求“彻底修复此类 bug，并加强提示词的说明”。此类问题指：
1. 原解析/书面表达【思路点拨】等合法原文因“字段标题未剥离 / 严格与非严格口径不一致”被误判 `EXPLANATION_SOURCE_UNVERIFIED`；
2. 模型输出根 JSON 多写一个 `}`（over-closed）导致 `Invalid final JSON response: Extra data`。

上一轮已修具体两处，本轮做系统性加固与提示词强化，避免同类再发。

## 改了什么

### 校验/解析加固（代码）
- `app/workflow/content_fragments.py`：`_EXPL_LABEL` 增加 `思路点拨`，source_ref 指向带【思路点拨】标题的解析行时一并剥离标题。
- `app/workflow/upload_content.py`：严格校验同样接受书面表达【思路点拨】原文（归一化后须完全一致），与非严格一致；改写/半截仍拒绝。
- `app/workflow/local_agent.py::_parse_response`：非严格模式在“少写花括号（补齐）”之外，新增“多写尾随 `}`/`]`（去掉后能解析成完整对象才接受）”；严格审核模式不修补。

### 提示词强化（防复发）
- `app/workflow/local_agent.py::INSTRUCTIONS`（所有阶段通用）：新增 JSON 纪律——只输出一个根对象、无第二对象/文字/多余 `}`/`]`、根对象以单个 `}` 结尾。
- `app/workflow/content_rules.py::RULES`（解析阶段）：新增“字段标题处理（程序自动剥【解析】【答案】【分析】【详解】【思路点拨】【文章大意】标题，别连标题抄）”“书面表达用 source_ref 指【思路点拨】行”“JSON 输出单对象纪律”。
- `app/workflow/agent_tasks.py::RULES['knowledge']`：明确整组题型必须包含 `type-knowledge-map.json` 中 `dims` 的必带维度（如听力长对话理解必须含主题），并加 JSON 单对象纪律。
- `app/prompts/roles/explanations.md`、`knowledge.md`：脑子检查补充标签剥离、书面表达思路点拨、必带维度、JSON 单对象等自检项。

## 没改什么 / 边界

- 不放宽真正的“需溯源”判断：非思路点拨的改写、半截、跨材料仍拒绝；严格审核模式仍然不修补 JSON；不猜标签、不编造内容。
- 非严格解析修复只在“去掉纯尾随括号后能得到完整对象”时生效，`valid+valid`（两个对象拼接）等歧义输入仍拒绝。

## 验证

- 确定性复盘（不调用模型）：知识点失败响应（多写 `}`）经新解析器可解析；解析 q-81（【思路点拨】source_ref）经严格校验 `passed=True`。
- 真实批次 `【木牍教育】20年安徽中考英语` 重启后自动续跑：`explanations`/`knowledge` 转 `completed`，`assemble`/`review`/`format_gate` 完成，`blocking_count=0`，仅剩真实标签差异待人工。
- 单测：`test_upload_content`（47）、`test_content_fragments`（23）、`test_agent_tasks`、`test_agent_roles`、`test_local_agent` 通过；新增“提示词含标签剥离/JSON 纪律/必带维度”回归。全量测试结果见提交记录（唯一失败 `test_plugin_router...` 为本机缺 DSH profile `cordis.patch.yml` 的既有环境问题，改动前已复现）。

## 回退方式

回退本提交即可恢复原实现与旧提示词（会重现两类卡点）。
