# 2026-11-17：统一问题闭环、后期集中核对与历史架构审计

## 背景与目标

用户要求减少前期流程中的反复卡点，统一处理问题，并从第一性分析真正的阻断原因；随后明确授权规划和实现，同时深入审计 Git 历史、关键 PR 证据与架构演变，识别可安全移除的历史负担。

本轮以当前 master 的 26c407e 为历史基准。启动时 fetch 成功，HEAD 与 origin/master 为 0/0。保留既有及并发工作区修改，不清理用户批次或改写其完成状态。

## 改了什么

- `app/workflow/findings.py` 统一错误码、已有题目身份、完整证据、阻断属性、严重性和稳定 finding_id；汇总原报告并沿用既有 self_heal/监督器/运维，不建立第二个修复队列。软提示和审核差异不获得内容重生成权限。
- `app/workflow/checks.py` 保留完整 stdout/stderr 和全部结构化诊断；尾句仅用于展示，不再作为唯一证据。超时、执行异常、缺输入和未解释的非零退出都留证。不从打印题号猜 unit_id。
- `app/workflow/self_heal.py` 去除每报告 12 条/总 24 条的问题截断，范围查找可读到后续单元。移除没有生产消费者的 UPSTREAM 旧映射，保留按 producer/evidence 的受限修复路由。
- 前期 `audit_type_layout` 使用 `--content-only`，结构与选项完整性继续严格；重复显示的完整观察后移且为提示。难度一致性后移最终闸门但继续阻断；聚合检查异常时复用原难度规则的纯 Python callable，避免硬检查丢失。
- `handoff.py` 的 meta/underlines 只验证其来源视图、身份、覆盖、子题形状与来源绑定，不提前要求未来答案/解析/标签。`manifest.is_stale` 对解释投影的校验限定在完整 assemble。
- `assemble_local.project_final_view` 将已接受填充与原文画线、元数据按身份合流；`agent_tasks` 在既有守卫与回滚事务内同步 batch.json、batch_meta.json、封存状态。validate_only 不写入，needs_review 不能封存，来源变化拒绝安装，安装异常回滚。
- 事务生成 `workflow/late-assembly-delivery.json`；manifest 仅在先前交付/输入、来源元数据哈希、最终字节哈希和消费者复验均匹配时更新已有 underlines 基线，不为过期、跳过或无证明记录补造完成态。
- `stage_semantics.py` 对共享最终文件按字段所有权比较；保留来源、画线、真实元数据、题号和嵌套证据变化；validate 仍读取完整内容。原始任务 CAS 与上传冻结继续使用精确字节。
- `final_audit.py` 在最终闸门合并确认单元的原卷核对与 strict 原答案/解析检查，并复验来源字节未变。托管批次缺来源保持失败；独立 payload 明确未做来源核对。真实选项文字差异不再被媒体错误码一并豁免。
- `config/model-router.json` 恢复 default={}，修正历史混合提交意外固定模型的回归；不修改设备现有显式选择。
- 更新 pipeline、根规则和子 AGENTS 的两轮审核说明，完整历史结论见 `app/docs/git-architecture-audit-2026-11-17.md`。

## 历史审计结论

主历史 342 个可达提交，全部引用 345 个（含 stash 对象），时间为 2026-11-04 至 2026-11-17。已阅读全部提交索引，对主要子系统的关键补丁和当前消费者定向复核；不是逐行审完每个提交。报告核验 43 个提交链接及 23 个本地路径。

没有取得可复核的 PR diff/review/CI 证据；GitHub PR 搜索为空，不能称已审阅关键 PR，也不能断言历史从无 PR。有意保留的 shell/原生路由、types 兼容、文件任务协议、OCR 回执、安装回滚和用户备份不能据年龄删除。安全窄清理为无运行用途的 UPSTREAM；固定模型模板为意图回归，已恢复。未接通的自定义提示词接口、显式路由损坏回退、通用审核缓存、部分旧文案和测试入口漏 mjs 作为未解决风险记录，未宣称修复。

## 边界

不推进或改写真实批次，不自动上传/发布，不安装或重启宿主，不改设备配置、不清用户备份，不自动裁决审核差异。当前来源核对仍基于已确认单元；报告明确保留 independent_original_page_coverage 和 rendered_preview 为未检查项，不能声称已完成独立逐页覆盖或浏览器渲染证明。本轮没有证明实际耗时、模型用量或用户干预次数改善，也没有声称所有历史风险均已解决。

## 验证

- 集成命令：`PYTHONPATH=app python -X utf8 -m unittest workflow.test_findings workflow.test_check_findings workflow.test_self_heal_routing workflow.test_self_heal_gaps workflow.test_format_gate workflow.test_explanation_gaps workflow.test_early_metadata_semantics workflow.test_late_delivery_receipt workflow.test_late_delivery workflow.test_batch_fill workflow.test_assemble_local workflow.test_agent_tasks workflow.test_delivery workflow.test_stage_semantics workflow.test_contracts workflow.test_content_snapshot`：284 项通过，55.817 秒。后续新增一条 stale 耦合回归，`workflow.test_late_delivery_receipt` 再跑 4 项全部通过，0.029 秒；验证 meta/underlines 不提前要求解析且 assemble 仍严格。
- flow 子任务最终专项：145 项通过，12.076 秒，其中 test_late_delivery 10 项，覆盖实际回执、旧输入拒绝、事务回滚和只读路径。
- `node --test backend/model-default-follow.test.cjs`：4 项通过，包含仓库模板无固定模型回归。
- Python 受改核心模块 py_compile 通过；git diff --check 通过（已有媒体文件 CRLF 提示）。
- 全 workflow discover 已启动，但在日志停更超过 10 分钟后取消；日志含 F/E，缺最终 traceback 与汇总。该运行未完成，不能声称全量通过或将其失败认定为既有。
- 后端全 cjs：377 项，358 通过、18 失败、1 跳过。观察到 Windows 进程生命周期断言、缺 shell 和 python3 exit 9009 等失败；未做干净基线对照，不能将全部失败归为既有。完整日志保留在工作区 `.backend-regression.log`，Python 未完成日志为 `.workflow-regression.log`。

## 回退

本轮未提交或安装。回退时只针对上述改动逐项撤销，保留当前树中原有和并发修改；不要 reset 整个工作区、覆盖用户数据或回滚其他人的来源坐标修复。若撤销字段所有权/事务回执，应同时撤销对应接受与manifest刷新逻辑，避免半套交接协议。
