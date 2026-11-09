# 2026-11-09 修复：AI 运维从未触发（事件签名永远匹配不上）

## 背景 / 现象

用户反馈「AI 运维从来没有工作过」。核查运行中的插件：

```
GET /wf-plugin/operations/status
{"available":true,"enabled":true,"configError":"","incidentCount":0}
```

服务可用、开关开启，但 `incidentCount` 始终为 0；所有批次 `workflow/operations.json` 都是：

```json
{"identity": [...], "incident": null, "receipts": [], "failures": {}}
```

## 根因

`9387566` 把自愈签名改成包含“重试证据”（输入/原卷/代码指纹）：

```python
# app/workflow/self_heal.py
signature = sha256(stage_id + "\n" + detail + "\n" + evidence)[:16]
```

但 `app/workflow/operations.py::_discover` 仍按旧公式重算：

```python
signature = sha256(stage + "\n" + detail)[:16]
```

两者**永远不可能相等**（evidence 永远非空），于是 `operations` 统计不到任何 `repair_failed`/`retry_failed` 事件，永远不会创建 incident，AI 运维也就永远不会接手。此前测试夹具用旧公式伪造 signature，掩盖了这个问题。

历史证据：2024 卷有一条真实的 `repair_failed`（validate：E19 书面表达题干连续下划线），19 年卷有一条 `review_gate repair_failed`（按规则 6，审核差异不是故障，属于设计排除）。

## 改了什么

- `operations._discover`：不再自行重算签名。改为按 `stage + 事件自带的 detail` 匹配真实失败事件，并**直接使用事件自身的 signature** 作为 incident 身份。这样无论 self_heal 以后如何扩展 evidence，运维都能识别。
- `operations._load` 结果补齐 `identity/incident/receipts/failures` 默认键，旧格式缺键不再 KeyError。
- `workflow.test_operations`：夹具改为使用包含 evidence 的真实签名；新增回归 `test_discovery_accepts_self_heal_evidence_signature`，直接证明“带 evidence 的 self_heal 签名能触发运维事件”。

## 边界

- 仍只对**已经真实失败过自愈**的当前卡点升级，不把 `review_gate` 差异、限流/额度、正常运行当故障。
- 不自动修复、不上传发布；修复动作仍走原有受控权限与复验。
- 不改动各批次已有 `operations.json`（不伪造历史事件）。

## 验证

- `workflow.test_operations`：58 项通过（含新增回归）。
- `workflow.test_operations_development + test_self_heal_gaps`：52 项通过。
- `backend/operations-agent.test.cjs + operations-http.test.cjs`：38 项通过。

## 回退

回退 `operations._discover` 的匹配逻辑与测试夹具即可；行为回到“运维永不触发”。
