# 2026-11-17：内容候选分片传输与格式恢复幂等

## 触发与验收目标

批次 `【木牍教育】21年安徽中考英语` 的既有错误为 `content_submit_candidate` 参数在 position 9117 解析失败，发生于宿主调用后端之前。当前实卷前置组装已经完成，本次不重跑批次。

旧恢复仍要求模型重写整包，且同一轮 `agent/error` 与 `turn/end` 重复报告格式错误会取消本应恢复的会话。新增回归已复现重复事件导致 `failed`。

修复归属：`content_agent.py` 独占草稿持久化、范围检查、组包与最终验收；`lib/content-agent.js` 只注册受控工具及管理宿主事件。复用 `unit_tasks.merge` 与原 `_submit/_validate`，不增加第二套内容验收。

## 实现范围

- 题型（含前置组装）、答案、解析、知识点任务可用 `content_stage_candidate` 每次暂存 1–4 个已绑定单元，最终 `content_submit_candidate({})` 由程序组包并完整验收。
- 暂存不是验收成功；缺片、越界、交叠、错误产物不交付，不写正式目录。相同单元集合可替换原片，重启从持久草稿继续。
- 结构确认仍必须全卷提交与验收；旧整包入口保持支持。提示词说明工具参数及原四键候选契约。
- 格式恢复对同一轮重复事件幂等，限定本插件工具与有限恢复预算；格式错误不重放已提交或结果未知的 POST。
- 来源、模型绑定、截止时间、工具预算、审核及上传边界沿用原规则。

## 验证

- 实施前新增重复宿主事件回归已复现 `running → failed`，实施后通过。
- `node --test backend/content-agent.test.cjs backend/content-agent-timeout.test.cjs`：58 项通过。
- `PYTHONPATH=app python3 -m unittest workflow.test_content_agent workflow.test_content_integration workflow.test_unit_tasks workflow.test_content_structure`：99 项通过。包括真实 HTTP handler 的暂存→进度查询→空参数完整提交链路。
- 真实批次现有 34 单元分成 9 片，在临时队列经真实 `preassemble` 验收器返回 `accepted=true`；原文及字段逐项相等，批次 282 个文件前后哈希一致。只读 `workflow.preassembly check` 返回 `passed=true`。
- 范围外回归不全绿：Node 全量 410 项中 407 通过、1 失败、2 跳过，失败为已有路由源码文字断言（`roles.get(agent)` 已被严格 override 读取取代）。Python 全量首轮 3328 项，113 failure/59 error；其中本次新增的 scoped 测试夹具漏写 `identity_version` 已更正并经上述 99 项复验。保留其他工作区代码、仅撤去本次内容代理修改的隔离对照仍有 113 failure/64 error；共同失败集中于并发中的结构/审核/表格等模块，不能宣称全仓通过。对照副本未拷入台账/变更记录，相关缺文件失败不作为代码对照依据。
- 安装来源已确认是当前源码的 link；后端健康且无在途内容任务。宿主有其他会话及运维活动，未强制停用插件/重启 Harness；JavaScript 新工具的真实宿主激活与新模型调用尚未验证，不能以 Python 后端重启冒充宿主重载。

## 回退

回退本次内容代理代码及提示词改动；草稿仅存在被版本控制忽略的 `.content-agent/tasks/`，不改变正式批次数据。
