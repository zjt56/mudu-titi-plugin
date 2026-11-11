# 十二类流程、悬浮子流程状态与并行触发

日期：2026-11-10

## 背景

用户确认主图为十二类，听力位于组装判型之后，答案、解析、知识点并行；全部内部子流程只在鼠标悬浮文字层列出，各自附状态。此前九类与串行蛇形箭头不能表达真实依赖，解析依赖答案、同步切段和提取后元信息回写也造成触发错位。

## 改了什么

- `lib/client.js`：十二类四行依赖布局，组装判型后四路分叉，文字三路汇总后体检，听力仅接入内容审核。节点主图只保留类别；悬浮层逐条展开服务端子流程并保留真实状态，缺失时回退阶段名；键盘可聚焦、aria-label 提供相同文字。
- `workflow/substeps.py`、`workbench/server.py`：只读投影全部18个阶段内部职责；体检从实际CHECKS注册表取得检查项，审核读取真实labels/sample轮次。pending、失效、rebuild不能用旧报告证明完成；失败只定位已知检查项；格式非阻断失败显示提示。运行中只有真实当前项显示处理中，不能定位时独列阶段执行/验收。轻量报告每次live观察重读，坏报告/超限报告保守未知。
- `workflow/contracts.py`、`auto.py`：题型交接先于填充，答案/解析/知识点独立具备启动资格；听力准备在一次advance生命周期内独立执行，文字汇总、整理、体检可先推进，到审核前才等待音频并按原契约验收。Web直接入口同样生效。
- `agent_tasks.py`、`stage_semantics.py`、`agent_prepare.py`：解析直接消费提取阶段已准备的原文/答案材料，不绑定并行写入的答案JSON；真实原件、结构、题型及来源验收仍保留。
- `runner.py`：卷头元信息识别在extract交付前完成，避免完成后改写已登记产物导致自身失效。
- 独立代码审查发现晚到音频只回填batch.json、未更新batch_meta视图的问题。`batch_fill.project_audio_views` 在真实音频交付时仅同步音频附件，逐项保留各视图非音频内容和画线；`manifest.record_delivery` 在事务内备份/回滚，只有此前当前且投影后消费者再次验收通过的视图才更新交付摘要。最终视图变更使旧validate失效，审核前实际重跑体检；普通及scoped import明确排除accept_result提前回填，统一由record_delivery唯一执行音频sync，避免第二次sync已无变化导致漏掉投影。重复相同音频不重复失效。不会重跑文字模型或改旧体检报告伪造通过。
- `stage_preflight.py`：复用已有未提交的统一确定性快路，确保自动并行不绕过解析直抄/现有切片验收。
- 根规则、README与pipeline/content-labeling同步本次用户确认语义，旧十节点预算回归改为准确十二类覆盖检查。

## 边界

没有重新处理真实试卷，没有直接编辑manifest或审核队列，没有上传、发布或修改模型路由。审核差异仍保留，不把执行完成等同质量通过。无细粒度报告的内部职责，只能以父阶段成功交付作为完成依据；执行中的候选文件不能证明已完成。没有测得真实性能提升比例。

## 验证

- 前端图、悬浮层与原上传页专项：初次25/25通过；兼容并行会话新增的无任务概览后最终28/28通过，十二类契约覆盖3/3通过；client语法、diff空白检查通过。
- 子流程及关联测试：29项通过；完整workbench：57项通过。
- 任务协议与既有确定性入口基线：101项通过。
- 完整backend初次230项、228通过、2跳过、0失败；并行样式会话收敛后对当前最终客户端重跑：235项、233通过、2跳过、0失败。最终完整workflow（显式离线环境）：2122项，2104通过、17跳过、1失败，耗时127.672秒；唯一失败为已有的test_native_extraction_warning_cannot_prove_number，与本次十二类/调度修改无关，其基线失败已由效率会话在独立HEAD复现。未放宽题号推定规则，不宣称全量全绿。首次并行全量与针对性争用全局agent锁已终止，不作为通过证据；第二次全量发现历史结构夹具会读本机模型配置并尝试router，已停止，最终全量须显式设置WF_STRUCTURE_AI=0、WF_AUTO_IDENTIFY_META=0隔离。原上传布局静态测试仍固定旧三列蛇形，已随批准的四行依赖布局更新后复验。
- 后台调度专项：120项通过（runner_auto、preassembly、content_labeling、agent_prepare、auto_audio、stage_semantics、audio快路）；音频/任务绑定专项：137项通过（audio_segment、agent_tasks、agent_protocol、diagnose_memo）。
- 晚到音频修复最终关联回归：162项通过；包含真实来源/结构/前置导入、组装、元数据及画线命令、两次体检、音频真实交付，证明三个视图音频完整且非音频字段逐项相等；消费者拒绝时五文件精确回滚，重复交付不重复失效，实际auto入口只复验一次。
- 同步旧依赖夹具与记录标题后，agent_dispatch、label_part_reuse、changelog_policy及preassembly共46项通过。
- 最终三入口交付专项167项通过：preassembly、agent_protocol、stage_preflight、scoped_regeneration、batch_fill、runner_auto、audio_stage_fastpath；普通/scoped音频交回均通过真实导出/导入与单次sync断言，validate_only不改五文件，最终视图保真且真实复验一次。独立只读复核确认晚到音频finding已关闭，未发现本修复范围内新增中高问题。
- 现有运行GUI代理只读接口：health正常，batches返回18阶段及71个子流程条目。当前内置浏览器Electron不可用，未完成真实鼠标悬停与截图验收，不声称视觉通过。

## 回退

仅回退本记录所列代码及其测试/文档，不回退工作区其他会话的效率优化。不得用旧manifest或旧报告覆盖用户批次；如回退调度，须同步回退图中依赖与任务绑定定义，避免只回退展示导致语义失配。
