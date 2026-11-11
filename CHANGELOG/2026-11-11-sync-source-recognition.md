# 2026-11-11 同步课时识别与来源证据

## 背景

用户要求结合七上《课时A计划》原件提高插件识别准确率，后续按一个课时一批次，来源识别年级册次、教材版本、系列、单元和课时。原生提取中 Section 的 Markdown 加粗拆分、题内期中/月考来源标签干扰同步身份；重复大题号曾使后续正文被误切为附录。

## 改动

- `sync_practice.py`：识别副本去格式，不改变原文；按独立作答标题而非正文关键词生成带出现序次、来源行号和课时上下文的候选。完整展示32条规则及附加/排除条件；正文证据不足不猜子类。audit仅作用于同步材料，定位题型/组单、跨大题和共享材料前缀缺口。
- `extract.py`：同步材料允许大题号重置及无序号作答标题，中段范文不截掉后文，明确尾答案块仍分离。候选不伪造覆盖确认。
- `structure_ai.py`、`agent_tasks.py`：结构与前置组装判型共用候选/规则；缓存及任务摘要绑定规则实现，非同步卷不套用审计。
- `metadata_ai.py`：纯证据识别单课时来源，例如“七上人教版课时A计划 Unit 1 课时1”；uint规范、Starter Unit区分；原名与真实路径独立取证，整册不冒用首课；无模型也补确定字段，既有非空保留，逐字段证据同步metadata与manifest。缓存绑定原名、路径和规则版本。
- `sync-practice-rules.json`：作文标题补“写一份”形态。新增集成测试，扩展来源测试，文档及根规则26同步。

## 验证

环境：bundled Python，PYTHONUTF8=1、PYTHONIOENCODING=utf-8、PYTHONPATH=app；禁真实结构模型。

- 6模块：test_sync_practice、test_sync_practice_integration、test_extract、test_agent_tasks、test_agent_protocol、test_metadata_ai：172项，171通过、1跳过、0失败。
- 6模块：test_structure_ai、test_docx_extract、test_source_structure、test_structure_gate、test_preassembly、test_question_draft：104项全部通过。
- 原件只读回归：SHA256 `71d0bac47b6b0b8bd85873727d6600468956cdcbf113478da4517b0a8ee8d4e5`。原生4396行、43表、424媒体出现；识别305处大题、14主类（含14篇作文），infer_structure同为305节，正文rstrip逐字相等，误入附录0字符。未重写用户原件/历史批次。
- 单课时纯函数验证输出“七上人教版课时A计划 Unit 1 课时1”；整册scope=book，仅册级来源，无unit/lesson。
- 全量发现2180项成功，但执行未完成：Windows review嵌套独占锁处中断；隔离该模块的宽跑也在子进程通信中被KeyboardInterrupt中断，无完整总结。不能声称全量通过。日志在本机.tmp，不提交。未因此修改运行锁/审核调度。

## 边界与未解决限制

此次是识别链改进，不是整册自动切批，也不是重复小题号全协议迁移。同一课时多个大题从1编号时，答案等下游全局数字键仍有限制；保留原号、occurrence与source_line，不放宽闸门、不伪造coverage。共享材料审计提示不替代真实绑定验收。只从明确信息识别RJ/人教版本；路径丢失且原名缺系列时不猜课时A计划。

不改历史批次、模型路由、审核差异、上传/发布边界；未安装或发布插件。

## 回退

对本次独立提交执行git revert；已有02f637f的基础规则提交独立保留。无需回滚用户数据。
