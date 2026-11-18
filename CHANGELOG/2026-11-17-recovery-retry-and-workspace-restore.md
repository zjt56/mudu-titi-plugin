# 自愈停止确定性派发重试，隔离副本增加基线恢复

## 背景/触发

19-2 实批的答案和知识点阶段在原生内容任务启动前报 `Native agent requires selected provider/model route identity`。旧实现把该 `integration` 派发异常转换成逐题失败，再让 `record_only` 通过 `_retry_admission` 变成局部内容反馈重派；同一派发故障因此被重复处理。同期隔离运维修复误把局部片段作为整个 Python 文件写入工作副本，损坏副本后 `rollback` 因没有安装 journal 而不能恢复。

## 改了什么

- `content_agent.py` 用结构化错误码 `native_route_identity_missing` 标记路由身份缺失；`diagnose.py` 在失败细节中保留机器错误码。
- `label_execution.py` 只对模型响应协议错误 `response` 做内容修复；其他派发/配置错误原样冒泡。
- `self_heal.py` 对已知派发/配置错误绑定阶段、错误码、源码指纹、设备路由文件摘要和当前 provider/model。相同状态直接进入代码修复路径，不调用自愈模型、不写内容反馈、不重排答案/知识点阶段；代码或所选路由变化后签名改变，可重新诊断。内容阶段 `record_only` 没有可验证修复时升级代码修复，不伪装成内容修复。
- `operations_development.py` 新增 `patch_source` 与 `restore_base`。前者以当前文件 SHA 和唯一非空原文片段作 CAS 局部替换；后者验证只读 `.base` 文件的 SHA/长度，仅恢复隔离副本，将损坏版本保存至 `.repair-history/`，并清除测试证据和安装计划。两种操作都不会写运行源码。
- 运维入口拒绝把 `start_line/context` 等局部编辑参数传给全文件 `write`；宿主工具 schema 暴露 patch/restore 操作并标记为真实写进展。
- 更新 `harness-operations.md` 并补充路由故障、局部替换、基线恢复和宿主工具 schema 回归。

## 没改什么 / 边界

- 不改用户批次产物、不重跑批次、不自动改答案、标签或审核差异，也不触发上传或发布。
- `restore_base` 仅用于未安装的隔离工作区，需匹配当前副本 SHA 和原始只读基线摘要；运行源码仍只允许已验证测试、计划、CAS 安装、备份和宿主重启流程变更。
- 对应 `ISSUES/open/` 记录暂不移入 `fixed/`：代码仍是未提交工作，真实 19-2 批次也未在新实现下重跑。

## 如何验证

`TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=app /Users/zjt/.local/bin/python3.12 -m unittest workflow.test_recovery_runtime_failures workflow.test_label_execution workflow.test_content_agent workflow.test_content_integration workflow.test_operations_development workflow.test_operations.OperationsTests workflow.test_operations.FastRepairOperationsTests workflow.test_self_heal workflow.test_self_heal_routing workflow.test_self_heal_verification workflow.test_scoped_regeneration workflow.test_operations_autonomous workflow.test_operations_autonomy_development`：249 项通过。

`node --test backend/operations-agent.test.cjs backend/operations-agent-autonomy.test.cjs backend/operations-agent-investigation.test.cjs backend/operations-agent-progress.test.cjs backend/operations-http.test.cjs`：83 项通过。`node --check lib/operations-agent.js lib/index.js lib/client.js`、相关 Python `py_compile` 与 `git diff --check` 通过。

全量 workflow suite 未运行。`workflow.test_operations.RealOperationsRepairTests.test_real_source_transaction_and_consumer_verification` 在旧 `HEAD` operations 代码对照下同样失败；这是既有隔离夹具缺 `07_review/structure-gate.json` 和解析来源证据，与本次隔离恢复功能无关，未通过放宽来源校验处理。

## 回退方式

回退本记录对应的自愈、隔离修复工具、宿主 schema 和测试改动即可；不得用回退方式恢复隔离工作区中的损坏文件，应通过该工作区的只读基线恢复操作。
