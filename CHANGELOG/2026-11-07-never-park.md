# 2026-11-07 任何单点失败不得停住流程（永不停车）

## 背景 / 触发
用户反复强调：「一定要不能存在，比如说某个进程直接因为什么问题直接卡住了，比如说什么答案提取、以及装配
或者那个各种东西，不能直接就因为某一个点出现问题了，直接把项目停止了。如果出现问题……把它修复一下，
不能让这个流程就这样停了。」

此前 `record_only` 会返回 `waiting_external`，等于把整批按住等冷却，属于"单点问题停整流程"。

## 改了什么
- `app/workflow/self_heal.py`
  - 新增 `UPSTREAM` 映射：每个可恢复阶段对应真正的上游产出者（如 `assemble→knowledge`、`validate→assemble`），
    绝不回退到 `upload/upload_gate/publish_gate` 边界。
  - `_escalate_upstream` 通用化：带具体错误回上游重推；`force=True` 用于模型显式选择 `upstream_rederive`。
  - 新增动作 `upstream_rederive`、`defer_and_continue`；`record_only` 改为「先回上游 → 再带反馈重跑内容阶段 →
    最后延后并继续」，不再返回 `waiting_external` 停车。
  - 防循环：同一阶段 + 同一错误 + 同一结构证据只允许回退一次，第二次转延后；证据/错误变化才允许再回退。
  - `_persist_action_bookkeeping`：动作自身的台账写入与本次诊断事件合并保存，避免覆盖或丢事件。
- `app/workflow/supervisor.py`：检测到"只有延后、没有质量阻断"时清掉降频，继续推进其它阶段。
- `app/prompts/roles/*.md` 增加「出问题怎么办（硬规则）」：不许只回"无法完成"，须重看证据、能修就修、修不了回上游、
  只有外部依赖才延后且其它阶段继续，禁止编造内容蒙混。

## 没改什么 / 边界
- 延后 ≠ 通过：被延后阶段回到 pending 并写原因，质量状态不变，不是"自动放行"。
- 仍不允许为让流程前进而编造题文/答案/解析，或放宽上传/发布边界。

## 如何验证
- `workflow.test_self_heal*` 全部通过；新增 `NeverParkTest`（5 条）覆盖：任何内容阶段 record_only 不返回
  waiting_external；延后后其它阶段仍可调度；同错只回退一次；证据变化可再回退；UPSTREAM 覆盖且不越界。
- `workflow.test_supervisor` 新增延后不降频用例，通过。
- 全量 Python 1578 项通过（0 失败 0 错误 17 跳过）。

## 回退方式
恢复 `self_heal._apply` 的 `record_only` 分支与去掉 `UPSTREAM/defer` 相关逻辑；撤销 supervisisor 延后放行。
