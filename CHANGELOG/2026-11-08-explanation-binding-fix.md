# 2026-11-08 修复解析/文章大意来源绑定误杀（EXPLANATION/SOURCE_UNVERIFIED）

## 背景 / 触发
23 年安徽卷 `explanations`（解析与文章大意）反复失败：
`Content source/ownership check failed: EXPLANATION_SOURCE_UNVERIFIED` 于
`group-56-57-58/58`、`group-59-60-61-62/62`、`group-73-74-75/74`，以及 `group-73-74-75` 的
`SUMMARY_SOURCE_UNVERIFIED`。自愈已从 `retry_stage_with_fresh_context` 升级到 `upstream_rederive`，
但内容是**对的**、只是绑定判定不过 → 重跑无法救回。

## 改了什么（中间方案，已被取代）

> **已被取代**：本方案试图放宽“解析/大意严格来源校验”，但用户要求改为
> “确定正文后按题号从 Word 原文直接抄录、不做严格溯源”，见
> [2026-11-08-explanation-direct-copy.md](2026-11-08-explanation-direct-copy.md)。
> 下列改动仅作历史留痕，最终实现以 direct-copy 为准。

## 根因（三类，均在校验器/提取器，非模型内容错）
1. **附录附注污染解析**：逐题解析行后紧跟独立的 `【熟词生义】`/`canned adj.罐装的` 之类附注行，
   解析提取把附注吞进解析正文，令模型干净解析无法与"原文解析行"全等 → 误杀。
2. **答案与解析分行的格式未被成员识别**：如 `74.It wasn't easy.`（无【解析】）其解析
   `【解析】根据第三段…` 另起一行无题号。stray 解析能绑定到 74，但组级【文章大意】的 `members`
   收集在该答案行中断（只到 73），导致 group-73 的 summary 只覆盖 [73] → 绑定失败。
3. **单字漏抄**：`group-66-67-68-69` 的 summary 模型写成"人们在睡**前**洗热水澡"（漏"觉"字），
   与原文 32 字不符 → 严格全等失败（属抄写笔误，非格式）。

## 修复（app/workflow/upload_content.py）
1. 新增 `ANNOTATION` 正则（`【熟词生义｜点拨｜思路点拨｜拓展｜引申｜考点｜词义辨析｜语法点｜注意｜易错点｜重难点】`），
   在解析与文章大意的 `parts` 提取循环遇到即截断，不吞附注。
2. 章节成员 `members` 收集：遇"纯答案行（无【解析】）且下一行以【解析】开头"时**纳入成员并继续**，
   不再中断，使组级【文章大意】覆盖完整题号区间。
3. 新增 `_minor_edit()`：归一化后仅差 ≤2 处单字增/删（LCS 判定、无换词）即视为抄写笔误；
   `restore_field` 的文章大意在「唯一权威候选（owner/members/唯一性均满足）」前提下，
   用它把近似文本**确定性恢复为原文 source_text**。语义改写不满足，仍报错重跑。

## 验证
- 对最新 explanations 全量 34 单元复跑 `restore_source_content`：**passed=True，0 错误，restorations=1**（漏字 summary 已恢复原文）。
- 逐单元：group-56-57-58 / group-59-60-61-62 / group-73-74-75 均 passed=True。
- 回归：`test_upload_content` + `test_upload` + `test_wire_format` 共 **73 通过**（`test_changed_api_base_requires_new_plan` 首跑偶发锁/端口抖动，隔离与重跑均 OK）。

## 边界
- 只放宽"解析/大意的来源绑定"判定口径（附注截断、分行成员识别、≤2 字抄写笔误按原文恢复）；
  **语义改写、大段不符仍拒绝并重跑**，未放宽原文保真与上传边界。
- 未自动上传/发布。

## 追加（实证通过 + 两个操作教训）
### 实测结果（用正确模型路由重跑）
- 使用 `MUDU_AGENT_CONFIG=backend/.local-agent.effective.json` 重跑 explanations：
  全分片 `wf-router: provider=deepseek-official model=deepseek-flash`、已校验落盘、
  阶段 `completed`；产物 `04_knowledge/explanations.json` 34 单元。
- 用产物复跑 `restore_source_content`：**passed=True，0 错误**；batch 进入 `enriching` 继续推进。
- 后端 Node 回归 110 通过。

### 教训 1：手动跑 runner 必须带 MUDU_AGENT_CONFIG
直接 `python -m workflow.runner run ...` 若未设 `MUDU_AGENT_CONFIG`，会回落到
`app/workflow/local-agent.json → scripts/bin/wf-agent.sh`（默认 magpie 第三方中转），报
`UNKNOWN_MODEL: magpie-responses ... imported/gpt-5.6-sol`。插件后端运行时会注入该变量指向
`backend/.local-agent.effective.json`（走 wf-router 按 model-router.json 选 deepseek-official）。
故手工续跑务必：`MUDU_AGENT_CONFIG=.../backend/.local-agent.effective.json python -m workflow.runner ...`。

### 教训 2：wf-router 临时 profile 的模型自愈加固（backend/wf-router.sh）
历史遗留的 `wf-plugin-route-*` 临时 profile 中出现过 `agent-default-model` 仍为
`magpie/auto/deepseek-v4.1-flash`（desktop 基线值）的坏缓存，虽 `.wf-route-ready` 已存在。
现已：①使用 profile 前强校验 `agent-default-model` 的 provider/model 是否等于 route_pick 选择，
不符则就地重写；②批量修复了现存 20 个坏临时 profile（现 0 个 magpie）。这让 wf-router 不再复用
错模型缓存。
