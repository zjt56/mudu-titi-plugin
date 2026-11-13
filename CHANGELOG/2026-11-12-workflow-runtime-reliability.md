# 成熟工作流理念落地：运行可靠性与自动推进

## 背景

用户要求总结扣子等成熟工作流的理念，结合插件实际工作流形成优化方案并实施。前置研究发现队列线程启动、空闲唤醒、原生任务未知回执、整批冷却与角色契约等具体窗口。

实施前fetch并快进到origin/master的426bf2e，保留远端新增审核与监督完成台账收敛。原研究文档stash恢复时的索引冲突已合并保留双方条目。

## 方案

见 `app/docs/workflow-optimization-plan.md`。保留contracts的18阶段、主图12类和跨批严格串行；借鉴声明式契约、依赖交付、持续对账、幂等恢复和有界反馈。不引入新引擎、第二份状态权威或额外人工停点。

## 实施记录

- `server.py`及专用worker测试：保存实际线程句柄，启动失败回滚旗标并保留队列；已queued/health入口对账，空队列退出原子清句柄；FIFO、同批去重、移出/删除边界保留。健康增加真实worker、排队年龄和无worker遗留running异常；不盲重放遗留owner。
- `content-agent.js`及测试：claim响应丢失或malformed、工具POST结果未知后持续只读精确task/session对账，指数退避至原deadline；不重放POST、不重复模型、不重置预算。取消/关闭/删除/外session不能恢复。
- `supervisor.py`及测试：clear-before-check、settle后重检、stop唤醒；独立安全本地stage/input binding在冷却中获得一次机会，保留原冷却与预算，不重复高频派发。
- `auto.py`及测试：冷却期间默认桥执行确定性preflight/托管assemble，不走模型fallback；不启动音频转写prep，已有证据可验收；本地成功保留模型冷却。
- `agent_roles.py`/`agent_tasks.py`及角色模板/测试：答案字段与校验器共用常量，whole-unit层级从taxonomy生成；修missing与阅读子题标签冲突，模板漂移构建前拒绝。

## 验证

- Python3.12隔离集成组438项：437通过、1跳过、0失败（16.753s）。首轮使用/tmp临时目录有2项路径别名断言失败，规范/private/tmp路径后同组合全部通过，未改生产路径校验。
- Node全套325项：323通过、2跳过、0失败（51.511s）；index/client/content-agent语法检查通过。
- 代理扩展workbench回归有4 failures+1 error，用HEAD原版server对照同样复现，涉及efficiency_projection、manual_advance_reset字符串mock、media_scoped_preview导语、overview_snapshot；没有声称全仓Python通过。
- 各故障测试验证线程启动失败恢复、同名队列去重、认领/提交丢应答且连续GET失败恢复、原deadline、取消/删除/外session不复活、漏唤醒、冷却下本地链推进、自愈失败阶段不被旁路。
- git diff空白与文档相对链接最终检查通过。未做实卷效果测量，未安装或重启宿主。

## 已知后续范围

extract的metadata/板块视觉/visual_review及structure_gate的structure_ai实际模型入口统一准入未在本批重构；不机械冻结整个本地命令或闸门。跨transport总资源预算、慢分支波次屏障也未列为已完成。本批先补有明确故障窗口的五项。

## 边界

不改原文、来源优先级、OCR顺序、正式标签或审核差异，不自动上传/发布。不以模型自述、会话空闲、旧文件或running标志伪造交付。不重跑用户或归档批次；测试使用临时夹具与模拟传输。安装/宿主加载状态另行如实说明，不把源码修改等同运行窗口更新。

## 回退

回退本次运行代码与对应测试即可；不需要迁移用户数据。不删除持久队列/回执来回退，已验收结果继续遵循既有输入绑定和消费者复验。
