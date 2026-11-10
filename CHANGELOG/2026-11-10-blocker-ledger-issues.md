# 2026-11-10 卡点台账：每次卡点留档并跟踪是否已修复

## 背景

用户要求：在本地项目的 Agent 规则里加一条——每次插件的试卷流程遇到卡点时就记录下来，同时记录该卡点有没有被修复；本地创建一个“待修复问题清单”文件夹，未修复的放里面，已修复的标注已修复。

此前卡点只存在于对话、`workflow/self-heal.json` 和日志里：跨会话、跨批次无法汇总，同类问题反复排查，也无法回答“还有几个没修”。

## 改了什么

- 新增仓库根目录 `ISSUES/` 台账：
  - `ISSUES/README.md`：制度说明、什么算卡点（以及审核差异/瞬时错误**不算**）、命令用法、字段与边界；
  - `ISSUES/open/`：**未修复待办清单**本体，一条一个 Markdown；
  - `ISSUES/fixed/`：**已修复留档**，`status: fixed` + 修复方式/提交/复验证据。
- 新增 `app/workflow/issue_log.py`（`python -m workflow.issue_log`）：
  - `add`：登记卡点（批次/阶段/失败检查/现象/判定口径/修复方向），写入 `open/`；同一「批次+阶段+检查」重复出现时**就地累加 `occurrences` 并更新 `last_seen`**，不重复建单；
  - `fix --id … --resolution … --commit … --verify …`：**就地**改表头并写修复记录段后移入 `fixed/`，正文（现象/判定口径/修复方向）与出现次数逐字保留；已修复再标记、未知编号都直接报错；
  - `list` / `show` / `summary`：查看清单与计数；
  - 编号与路径做白名单校验（`YYYY-MM-DD-简短标题`），拒绝 `../` 等越界；只写 `ISSUES/`，不碰批次数据、manifest、收据或上传边界。
- 规则：根 `AGENTS.md` 硬规则 **24**（卡点必须落台账）+ 项目地图新增 `ISSUES/` 一行；`app/AGENTS.md` 新增「卡点台账（必须记）」小节（含命令与边界）。
- 回归：`app/workflow/test_issue_log.py` 9 项，覆盖登记字段完整性、去重累加且正文无损、标记已修复并保留正文、一次性状态、编号越界拒绝、标题必填、列表/计数、CLI 往返、仓库目录存在。

## 没改什么 / 边界

- 台账只是留档，**不替代修复**：不改变批次状态、审核结论、上传边界，也不自动修任何东西。
- 不记录审核差异（`review_gate=needs_review + execution_completed + continue_unqualified`）与限流/额度等瞬时错误——按硬规则 6 它们不是卡点。
- 不删除历史记录；`fixed/` 是留档而不是“已关闭/忽略”。
- 未复验不得标记 `fixed`（命令要求填 `--verify`，但最终仍靠人/Agent 如实填写）。

## 验证

- `PYTHONPATH=. python3 -m unittest workflow.test_issue_log`：9 项通过。
- 手工 CLI 往返（临时目录 `WF_ISSUES_DIR`）：`add` → 同卡点再 `add`（occurrences 1→2、正文保留）→ `fix`（移入 fixed、`status: fixed`、现象/判定口径/修复方向与 occurrences 均保留）→ `list` 计数正确。

## 回退方式

回退本提交即可移除台账与规则；`ISSUES/` 是纯数据目录，删除不影响任何批次与工作流产物。
