# Harness 运行时内容子智能体接入

日期：2026-11-12

## 背景与目标

用户要求把 Harness 子智能体实际用于插件运行时的试卷提取、来源查证与匹配，按评估方案实施，而非只用来协助开发。新增按需取证、多轮候选反馈和可恢复会话，仍由原有程序验收并安装正式产物。

## 改动

- `lib/content-agent.js`、`lib/index.js`：原生 create/resume 子会话、仅允许七个 content 工具、实际模型路由、任务并发/轮次/工具/时间预算；恢复原待处理消息，不重复追加。提交结果未知时只读查回执，不盲重发。媒体 HTTP 允许 120 秒，仍受剩余任务总预算及取消信号约束。
- `app/workflow/content_agent.py`、`app/workbench/server.py`：持久队列、同进程线程锁与跨进程锁、输入/契约/路由绑定、只读冻结快照、候选校验、单调终态与精确任务回执。耗时视觉调用不持有全局工作台写锁。大规则文件和显式长答案通过任务内只读目录分页读取，不再因提示词压缩而不可达。
- `app/workflow/local_agent.py`、`backend/local-agent.plugin.json`：知识点、答案、解析、前置组装及结构入口接入；types 仅兼容旧任务/局部修正。auto 仅在实时宿主存在时选择原生；strict 必须原生。选中后错误不偷偷切 CLI/模型。
- `app/workflow/content_structure.py`、`structure_ai.py`：整卷目录与原文窗口取证，候选边界不作为完整性真相，沿用原 sanitizer 并做独立全卷覆盖检查；候选失败保留原件与隔离证据。可信提取清单授权图片，清单及图片字节都绑定缓存和验收。
- `app/workflow/content_media.py`：仅查看任务声明的真实图片，路径/链接/魔数/字节/实际模型路由校验；证据缓存、瞬时延期和有限媒体预算，观察不生成正文或答案。
- `app/workflow/auto.py`：Windows 同进程并行单元的 manifest/导入记账用线程锁串行，模型调用仍并行，修复进程级文件锁不能排除同进程线程的问题。
- 明确删除批次时，先清理对应队列、候选、缓存及已证明归属的 Harness 持久会话，再删原批次；清理失败保留数据并报错。持久会话清理是可信宿主动作，不开放给模型。
- 新增使用文档 `app/docs/harness-content-agents.md`，在本地 Agent 文档及 README 链接；运行快照与验证日志排除版本控制。

## 边界

保留答案/解析确定性直抄、缓存、局部验收基线、听力并行及原有两轮独立审核。教材匹配暂停。真实审核差异不会自动消除；不修改已有用户批次，不自动上传发布，不通过改 manifest 造完成态。本轮没有进行真实试卷效果测量，模拟测试不能证明准确率或成本收益。

## 验证

- 最终受影响 Python 组合：`test_content_agent`、`test_content_structure`、`test_content_media`、`test_content_integration`、`test_workbench_delete_batch`、`test_runner_auto`、`test_stage_preflight`、`test_local_agent`、`test_visual_transport`、`test_lesson_bands`、`test_structure_ai`、`test_structure_gate`：**264 项，258 通过，6 跳过，0 失败**（63.804 秒）。含真实本地 HTTP Handler→队列→认领→读证据→提交→查询回执闭环；模型和候选校验依赖为模拟。
- `node --test backend/content-agent.test.cjs`：**36/36 通过**；`node --check` 检查 index/client/content-agent 三个宿主脚本通过。
- `git diff --check` 通过（仅既有 CRLF 提示）。
- 广泛后端回归实际运行 313 项：294 通过、18 失败、1 跳过。失败涉及当前 Windows 无 sh/python3、后端生命周期 VM 断言以及运维工具调用预期；不能声称全库回归通过。用明确 Python 重跑部分模块为 21 项、11 通过、10 失败，日志保留。
- 全量 Python discover 已尝试，但发现其中测试启动真实 wf-router 模型子进程，已停止，未得到完整结论。最终以上受影响组合全部使用隔离测试，未把未完成全量检查写成通过。

## 远端同步与冲突合并

用户要求提交全部工作区改动并推送。同步 `origin/master` 时，合并了远端的排队控制、知识点/题号及表格保真改动，保留本地原生来源对象绑定与运行时子智能体能力：

- 后端 `source_blocks` 同时兼容旧表号映射及完整 extraction 证据；失效原生引用不能借旧映射的合并跨度通过。
- 表格草稿保留严格来源摘要和原生网格完整序列化验证；旧协议继续核对行列形状，真实空行、单元格文字中的竖线与嵌套表格按原生对象处理。
- 前端同时保留 structured 与 rows/cellSpans 两种投影，图片和文字仍在同一单元格内。
- 合并后回归：原生队列/结构/媒体 87 项通过；后端预览、HTTP、删除链路 29 项通过；草稿与来源对象 64 项通过（与前一组有模块重叠，不合计）；宿主及前端四模块 52 项通过。三个宿主脚本语法检查、`git diff --check` 通过。
- 未重跑或改写真实批次，未安装插件或自动上传。同步采用变基及普通推送，不强推。

## 启用与回退

源码需由宿主重新加载该插件版本，旧进程不会自动获得新管理器。将 `native_agent.enabled` 设为 false 后，新任务沿用原有执行方式；不要删除在途队列来回退。宿主不可用时 auto 任务选择原有执行方式；strict 会如实失败。回退不改变上传边界或既有正式批次。
