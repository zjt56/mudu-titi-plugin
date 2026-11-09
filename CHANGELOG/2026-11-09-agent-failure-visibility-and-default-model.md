# 2026-11-09 代理失败信息可见化 + 恢复默认模型路由

## 背景

用户另一台设备在前置批次组装时报告：

```
前置批次组装（含题型）：阶段失败
原因：Agent subprocess failed (exit 1, exit); inspect stderr.log
```

原提示只有退出码和“看 stderr.log”，没有真实原因，无法判断是模型路由、鉴权、额度还是脚本错误。同时，上一提交 `fedd259` 曾把 `config/model-router.json` 默认模型改为 `magpie/auto/deepseek-v4.1-flash`，该改动会随插件源码同步到其它设备，可能导致对方机器上的 magpie 网关不可用而直接 exit 1。

## 改了什么

1. `app/workflow/local_agent.py::invoke`：子进程非零退出时，在保留机器可读前缀 `(exit N, code)`（限流分类依赖它）的前提下，附带**脱敏、限长 400 字符**的 stderr 尾部，再提示完整日志路径。这样界面能直接看到真实报错，而不是只有“inspect stderr.log”。
2. `config/model-router.json`：默认模型恢复为 `deepseek-official / deepseek-flash`（此前的已知可用官方路由）。若确实要走 magpie，请在「模型与设置」里显式选择，不再作为仓库默认。
3. `app/workflow/test_local_agent.py`：失败用例新增断言，要求消息同时含 `(exit 7, exit)` 与真实 stderr 内容。

## 没改什么 / 边界

- 不改变失败分类逻辑：`rate_limit`/`quota`/`timeout` 的判定与冷却行为不变。
- 不自动重试真实内容错误；仅让原因可见。
- 不触碰任何用户批次数据。

## 验证

- `workflow.test_local_agent + test_preassembly + test_label_execution + test_runner_auto`：**102 项通过**。
- 前置失败分类用例仍按原分类返回 `exit`；限流前缀正则 `(exit N, rate_limit)` 保持不变。

## 回退

回退 `local_agent.invoke` 的消息拼接与测试断言；默认模型如需 magpie 可在设置页单独选择。
