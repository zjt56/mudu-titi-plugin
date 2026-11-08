# 2026-11-07 内置 7 个子 Agent 角色 + 按角色独立路由

## 背景 / 触发
用户提出：给插件默认设置几个子 Agent 各做不同任务，提示词与规则做好，子 Agent 自带"脑子检查"。
经确认先做「角色化提示词 + 独立路由」，暂不做独立 Checker。

## 改了什么
- 新增 `app/prompts/roles/{answers,audio,types,explanations,knowledge,assemble,review}.md`，
  每个含「职责 / 权限边界 / 脑子检查 / 常见错误 / 出问题怎么办（硬规则）」。
- 新增 `app/workflow/agent_roles.py`：加载角色，校验 `stage`、`artifact` 必须与 `agent_tasks.ARTIFACTS`
  完全一致，否则报错，绝不静默退回通用提示词。
- 任务提示词注入 `Agent: <role>` 头 + 角色段落（`agent_tasks._build_task_template`）；
  审核轮次同样注入（`review_rounds._round_prompt`）。
- `backend/route_pick.py`（由 `wf-router.sh` 调用）：路由优先级
  `review: rounds[round] → roles[agent] → review.default`，其余 `roles[agent] → stages[stage] → default`；
  缺默认模型直接失败，绝不静默换模型。
- `config/model-router.json` 增加 `roles`；`lib/index.js` 为 roles 补 profile；设置页改为「各子 Agent 模型」。

## 没改什么 / 边界
- 未改变任何阶段的 JSON 契约与校验器；角色只补充身份/权限/自检。
- 未引入独立盲判 Checker，审核仍由现有 review 阶段 + 确定性闸门裁决。

## 如何验证
- `python -m unittest workflow.test_agent_roles workflow.test_route_pick` 通过。
- `node --test backend/sub-agent-roles.test.cjs` 通过；全量 Node 110 项通过。
- 全量 Python 1578 项通过（0 失败 0 错误 17 跳过），含本次新增用例。

## 回退方式
回退 `agent_roles.py` 注入点、删除 `app/prompts/roles/`、`config/model-router.json` 的 `roles` 段，
并把设置页改回 `stages`；`route_pick.py` 回到仅按 stage/review 选模型。
