# ISSUES — 试卷流程待修复问题清单

本目录是**卡点台账**：插件试卷流程每遇到一次卡点，就在这里记一条，并跟踪它是否已修复。

用户要求（2026-11-10 明确）：
> 每次插件的试卷流程遇到卡点时就记录下来，同时记录该流程卡点问题有没有被修复；
> 本地要创建一个类似的待修复问题清单的文件夹，没有修复的放里面，已修复的标注已修复。

## 目录

| 位置 | 含义 |
|---|---|
| `ISSUES/open/` | **未修复**。待修复问题清单本体，一条一个 Markdown 文件。 |
| `ISSUES/fixed/` | **已修复**。文件头 `status: fixed`，含修复方式、修复提交、复验证据。 |

状态只有两种：`open`（未修复）和 `fixed`（已修复）。没有“已关闭/忽略”——真实卡点要么修好，要么留在 open 里。

## 什么算卡点（必须记）

- 阶段 `blocked` / `failed` / `needs_review`（**执行未完成**那种，不是审核差异）；
- 确定性校验失败反复出现（同一阶段同一报错，重试无法消除）；
- 自愈升级（`escalation.kind=code_repair`）或重复失败被拒；
- 队列卡住、批次长时间无进展、同一批次反复重跑同一阶段。

**不算卡点、不要记**：

- `review_gate=needs_review + execution_completed + continue_unqualified`：这是审核已执行、差异待人工判断的设计态（硬规则 6）；
- 限流/额度等瞬时错误（等恢复即可，且会自行重试）；
- 用户主动停止、主动删除批次。

## 怎么记

统一用台账命令（在仓库根目录，`PYTHONPATH=app`）：

```bash
# 登记一条卡点（未修复）
PYTHONPATH=app python3 -m workflow.issue_log add \
  --batch '【木牍教育】20年安徽中考英语' \
  --stage explanations \
  --title '书面表达思路点拨解析被拒' \
  --check EXPLANATION_SOURCE_UNVERIFIED \
  --why 'source_ref 指向附录第115行，【思路点拨】标题未被剥离，解析无法与原文匹配' \
  --how '剥离【思路点拨】等标题；严格模式同样接受书面表达思路点拨原文' \
  --batch-dir 'workflows/【木牍教育】20年安徽中考英语'

# 修复并复验后，标记已修复（自动移入 ISSUES/fixed/）
PYTHONPATH=app python3 -m workflow.issue_log fix \
  --id 2026-11-10-书面表达思路点拨解析被拒 \
  --resolution 'content_fragments 增加思路点拨标签剥离；upload_content 严格模式接受书面表达思路点拨' \
  --commit bb6e869 \
  --verify 'test_upload_content 47 项通过；真实批次 explanations 阶段 completed'

# 查看清单
PYTHONPATH=app python3 -m workflow.issue_log list
PYTHONPATH=app python3 -m workflow.issue_log list --status open
PYTHONPATH=app python3 -m workflow.issue_log show --id <id>
```

## 记录要求（硬性）

1. **必须点名具体检查与判定口径**：哪条检查、哪道题/哪个单元、为什么判失败。只写“按阻断项修复”不算记录。
2. **必须写清修复方式与复验证据**才能标记 `fixed`；没复验就留在 `open`，不能“看起来修好了”。
3. **同一批次+阶段+检查重复出现**时，命令会自动累加 `occurrences` 并更新 `last_seen`，不重复建单；不要手工造重复条目。
4. 修好之后**不要删除记录**，移到 `fixed/` 留档，避免同类问题再次发生时从零排查。
5. 记录只是台账，**不能替代修复**：留档不等于修好，也不改变批次状态、审核结论或上传边界。

## 字段

| 字段 | 说明 |
|---|---|
| `id` | `YYYY-MM-DD-简短标题`，文件名即编号 |
| `status` | `open` / `fixed` |
| `batch` / `batch_dir` | 具体批次名与可定位目录（硬规则 11） |
| `stage` | 卡点所在阶段 |
| `check` | 具体失败检查码/检查名 |
| `created` / `last_seen` / `occurrences` | 首次/最近出现时间与次数 |
| `fixed_at` / `fix_commit` / `verification` | 修复时间、提交号、复验证据 |

正文三段：**现象（卡点）**、**具体检查与判定**（失败检查/判定口径/修复方向）、**修复记录**。

## 边界

- 只写本目录，不触碰 `workflows/` 批次数据、`workflow/manifest.json`、收据或上传边界。
- 不会自动删除任何记录；也不会因为台账里没有条目就认为流程没有卡点。
