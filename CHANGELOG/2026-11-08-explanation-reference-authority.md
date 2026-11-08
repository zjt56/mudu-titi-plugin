# 2026-11-08 解析来源：重复副本不再制造“无法唯一绑定”卡点

## 背景 / 触发

用户上传 22 年安徽中考英语后，`validate` 阶段失败并停在：

```
✗ group-31-32-33-34-35-36-37-38-39-40/40 explanation: EXPLANATION_SOURCE_UNVERIFIED
✗ group-70-71-72/72 explanation: EXPLANATION_SOURCE_UNVERIFIED
怎么办：确定性检查未过：按上面列出的阻断项修复对应问题后重跑该阶段。
```

核对原卷后确认：这两条解析在 `01_source/附录.txt`（Word 权威来源）里逐字存在，
内容本身没有错。真正的根因是**同一份卷被重复提取成多份来源**：

- `01_source/附录.txt`（Word，权威）
- `01_source/materials/attachment_001.txt`（PDF 副本，`material-roles.json` 明确
  `content_authority=false`，措辞与标点都不同，还吞进了【词汇积累】【难句分析】）
- `01_source/materials/source.txt`（正文副本）

`upload_content.unique_anchor` 把“同题号、不同文件”的第二份解析一律当成竞争解析，
于是权威来源里明明逐字匹配成功，仍被判为“来源不唯一”。项目文档一直写着
“PDF OCR 的重复内容不制造来源不唯一冲突”，但代码没有落实。

## 改了什么

1. `app/workflow/upload_content.py`
   - 新增 `reference_only_sources(batch)`：读取 `01_source/material-roles.json`，
     收集 `content_authority=false` 的 `text_path`。
   - `unique_anchor(..., expected=…)`：只有**措辞不同**的解析才算竞争；同一段解析的
     副本（含全/半角标点差异）不再制造冲突。权威来源内部出现第二份不同解析仍判冲突。
   - 解析/大意/“原文缺失”三处锚点调用都传入实际文本做比较。
   - `ANNOTATION` 支持 `##【词汇积累】`、`【难句分析】` 等 Markdown 前缀附注标题，
     避免 PDF 副本把附注吞进解析正文。
2. `app/workflow/diagnose.py`
   - 新增 `CHECK_HINTS` 与 `_check_hint()`：闸门失败时说明具体检查项、判定口径和
     定位方法，替代原来的“按上面列出的阻断项修复对应问题”。
3. 审核侧同类误报的预防（同期发现并修复）
   - `agent_tasks` 不再把 `06_batch/question-draft.json` 放进 review 上下文：草稿的
     题型/知识点按设计为空，模型曾据此把整卷 30 个题目误报为“未标注”。
   - `review_rounds` 提示词明确“审核对象是最终批次字段”。
   - `review._review_rule_revision()` 纳入实际输入绑定：改审核规则/提示词后旧轮次
     自动作废，不会继续显示旧差异。
4. `app/workflow/test_upload_content.py` 新增回归：参考副本不影响唯一性、权威来源
   内第二份不同解析仍冲突、`##` 附注标题正确截断。
5. 根 `AGENTS.md` 硬规则 2/4 与 `CHANGELOG/RULES.md` 记录该口径。

## 没改什么 / 边界

- 不放宽“解析必须来自原文”：权威来源里找不到、或权威来源内两份互相矛盾的解析，
  仍然阻断。
- 不改变审核口径：真实知识点/基本差异依旧进人工队列；能力受限只作 informational。
- 不自动上传/发布；未改写 14/19/23 年等已推进批次的数据。

## 如何验证

- `workflow.test_upload_content`、`test_explanation_gaps`、`test_no_ai_generation_rule`、
  `test_validation_contracts`：79 项通过。
- `workflow.test_review_rounds`、`test_review_local_reuse`、`test_review_consensus`、
  `test_agent_entry`：115 项通过。
- 22 年真实批次：`runner validate` 由失败转为通过（15 项检查全绿，含 check_explanations）。
- 22 年批次重跑 `review` 后，原先基于草稿的 30 条误报消失；剩下的是真实的一审
  证据路径校验失败与二审 1 条图片能力受限项（未在本记录中一并修复，见下）。

## 仍未处理（如实记录）

- 22 年批次一审（labels）两次尝试均因 `Evidence must cite a bound batch input` 未通过，
  属模型引用了未绑定路径；需要定位该轮响应后修复模型输出或放宽到“引用可验证的
  绑定文件别名”。
- 二审 q-2 的“图片选项无法视觉核验”应归类为能力受限 informational，当前仍进了
  open findings；需要把 `_is_capability_gap` 的判定接到轮次 uncertain 路径。

## 回退

还原 `upload_content.py` 的 `reference_only_sources`/`unique_anchor(expected=…)`/
`ANNOTATION`、`diagnose.py` 的 `CHECK_HINTS`、review 上下文与绑定改动即可；
不要回退 `AGENTS.md` 的规则说明。
