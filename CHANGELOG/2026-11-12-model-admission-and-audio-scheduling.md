# 模型调用准入与慢音频调度续进

## 背景

用户要求继续推进上一轮优化方案。上一批完成了队列/回执/唤醒/本地冷却/角色契约，但提取与结构内实际模型入口尚有冷却覆盖缺口，音频模型进入并行波次后仍可能阻塞文字合流。

## 实施范围

- 新增model_admission共享准入与8条离线回归；在local_agent启动CLI前、content_agent新建任务前、visual_transport及legacy视觉网络前核对批次冷却。明确批次身份，结构化延期，缓存和本地复验先行，route-only不挡。
- metadata/structure/lesson_bands/visual_review/content_media传递批次并保留deferred，不把尚未调用模型记成永久失败。
- auto并行波次音频future由调度owner保留精确任务和导入闭包，文字汇总和体检不等音频模型，在review之前收敛；singleton音频也适用。沿用原contracts与唯一正式导入路径；退出finally收尾后才释放批次执行锁。
- 精确model_cooldown错误作为延期处理，不重新延长原冷却marker。

## 边界

兼容已有批次级冷却marker，本次不宣称跨provider/model路由隔离或全局并发预算已完成。route-only、本地解析、有效缓存、已接受回执及候选复验不当成新模型调用。模型延期不能转永久故障，也不能每次准入拒绝都延长原deadline。

不修改真实试卷、不自动上传发布、不改审核差异、不迁移历史批次。当前源码仍包含上一轮未提交改进，集成验证覆盖两批组合。

## 验证

- 最终Python3.12隔离集成316项：315通过、1跳过、0失败（5.063s）；调度55项全部通过（1.530s）；准入专项组合185项184通过、1跳过（与集成重叠不合计）。
- 扩展133项尝试中audio_skip同一测试两个子用例失败，HEAD对照未完成，不能认定已证实为基线失败，也未声称全仓通过；本轮不改该夹具。
- git diff --check与宿主index/content-agent语法检查通过；本轮未改Node运行逻辑，沿用上一批325项Node结果，不冒充再次执行。
- 新增故障/时序测试使用临时目录和模拟模型。扩展runner旧用例时发现未mock self_heal.recover会从测试临时目录启动本机模型桥；已停止对应测试job及其子进程，不触碰真实后端或用户批次。现将该suite默认自愈入口也离线化，专门自愈测试以嵌套mock覆盖；不能声称此前所有尝试都零外调。
- 事件测试验证assemble/validate先于audio完成、review在其交付后；音频未验收不得放行。
- 剩余边界：已queued原生任务可在冷却出现后继续claim，当前协议未引入可延期claim，已加特征回归明确；批次marker并非路由级预算。未部署或重启宿主。
