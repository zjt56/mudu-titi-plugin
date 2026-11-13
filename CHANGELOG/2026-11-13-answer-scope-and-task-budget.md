# 单专题答案附录归属与内容任务预算修复

日期：2026-11-13

## 背景与真实根因

批次 `33七上人教版课时A计划Unit3MySchool综合阅读提升` 的答案任务在 09:04:48 创建，09:14:48 失败。后端预算为 1500 秒，宿主默认硬上限却是 600 秒，实际执行约 10 分钟，并非先前对话推测的 25 分钟。

第一版候选已有全部 14 题正确答案。正文唯一专题为「综合阅读提升」，附录直接从完整Ⅰ/Ⅱ答案开始，未重复专题标题。`upload_content._compact_scoped_candidates` 原先要求正文/附录专题逐项相等，将全部答案归属拒收；模型累计提交 22 版，末版才退化为将已有答案误记 missing。`☆拓展设问` 原本已被正确跳过，不是本次根因。

## 改了什么

- `app/workflow/upload_content.py`：仅当正文全部大题属于唯一非空且只出现一次的专题、附录未显式声明任何专题时，允许通过完整 Roman 骨架和每节全部题号覆盖证明专题标题省略。混合/重复专题、错专题、partial、错误值/坐标和身份漂移仍拒收。
- `lib/content-agent.js`：缺省任务预算仍为 10 分钟，显式后端时间预算可执行至宿主硬上限 1 小时；较低显式宿主上限、后端绝对 deadline 仍收紧，认领/恢复不重置计时。状态暴露实际生效预算和 deadline。
- `app/workflow/content_agent.py`：同一验收器连续 3 次相同错误或累计 6 版未通过候选即终止本次尝试，返回 `validation_no_progress / retryable=false`。逐版保留具体验收反馈；代码修复后保留候选走本地复验，不重购模型调用。
- 同步内容智能体文档，新增单专题归属、预算时钟及校验循环回归。

## 真实批次恢复

通过 `answers_direct.build_payload` 证明 14 题答案与14个真实来源坐标、missing={}，再调用 `answers_direct.install` 走普通 export/import 事务。正式答案再次通过 `agent_tasks._validate`；没有直接改 manifest 或覆盖来源。后台监督器随后完成组装和本地体检，进入真实内容审核。未上传/发布。

## 验证

- 综合 Python 回归（content_agent / answer_sole_topic / scoped_whole_word_answers / answers_direct / upload_content / agent_tasks / agent_protocol / issue_log / repo_hygiene）：222 项通过。
- 答案绑定扩展10模块：153 项通过。
- 全 backend：360 项，358 通过、2 跳过、0 失败。
- JS 语法检查及 `git diff --check` 通过。

## 边界与回退

没有放宽答案内容真实性、跨大题归属、Word优先级、缺失证明、审核差异或上传边界。修复后的宿主预算逻辑在宿主重新加载该模块后生效；真实批次的Python来源修复已由本次正式导入和运行中后端消费验证。需要回退时撤回本次代码提交，保留候选和已记录的原文坐标，不伪造失败或完成状态。
