# 运维修复阶段空转与当前课时结构阻断

## 背景与触发证据

用户反馈最新12课时（Starter Unit3 第四课时 Project）AI运维持续显示修复中，录题流程暂停。事件22:53:40建单，22:57:52建立隔离工作区，23:01:18宿主会话预算耗尽后释放并重复认领。23:05后已有复现测试写入和失败测试，不能继续称完全没有工作；但无产品源码修复、安装或原检查通过。

读取工具存在确定缺陷：TextIO.seek使用字节偏移而read长度按字符，operations响应又截12000字符且不提供下一游标，导致中文源码分页出现重叠、遗漏和U+FFFD。没有安全源码搜索只能反复翻大文件。宿主whenIdle/HTTP等待只传取消信号，忽略取消时未必真正结束，预算定时器不能保证close执行。

真实结构阻断：年龄15误识题号；无编号古诗表格及作答线未建立合法来源单元；遗留跨大题重号警告未在明确section作用域证明下消除。不能简单删警告或伪造题号。

## 改了什么

- operations_development提供白名单字面搜索、按行读取、UTF8精确字节续页(next_offset/eof/sha256)，operations action完整接线，不再二次无标识截断。
- 宿主等待whenIdle、会话创建/恢复及HTTP时真正race取消；晚到handle销毁，不接受晚到setup；已发出的写请求未知则保留unknown，不release或重发。原10分钟预算不因心跳刷新。
- 连续6次纯读取提示具体剩余动作，12次后不再POST纯读而结束当前轮；有限纠正仍无产出则结构化no_substantive_progress释放，后端按同证据等待防付费循环。有write/test等实际动作的未完成尝试保留正常恢复计划，不因未闭环误冻。
- 卡片按实际准备、定位代码、修改、测试、复验阶段显示，过期/未知优先，单有repair权限不等于实际修改。
- extract/structure_gate/question_content修复年龄误号、严格来源证明的无编号表格作答题、独立大题位置及身份重建证明的合法重号。完整源片保留，不生成答案或印刷题号，不降低coverage；补真实脚本入口导入回归。详见structure-source-shape专项记录。

## 现场操作

23:11通过正式宿主config临时关闭运维，排空成功，保留所有回执与隔离复现，避免源码变化期间旧快照重复调查。备份10份失败证据至批次workflow/recovery-evidence-20261112-2315（实际备份由该目录内hashes.json记录）。首次正式extract暴露新相对导入脚本入口缺陷，修正为workflow.source_anchors并新增真实subprocess回归后再执行。正式extract于23:14:15通过（板块视觉0新增调用），structure_gate于23:16:10通过，8单元（6独立/2题组），古诗无印刷号与大题scope均逐项留证。

plugin_manager重复install返回already-installed，改用同一已安装bundle禁用/启用，两个receipt均application=applied；存在无关agent-teams第三方契约警告，未动该插件。运维原enabled=true已恢复并GET确认。后端重载后实际端口变化，通过原宿主/wf-plugin/api代理核验，未启替代服务器。旧事件GET已resolved且verification.verified=true。23:18:09前置组装真实running，随后确认advance_worker.worker_alive=true且running_batches为本批，无running_without_worker。用户无需反复点继续；后续答案/审核未完成，不冒称整卷成功。没有直接编辑manifest或更改原始试卷。

## 验证与边界

- Python3.12隔离集成467项：461通过、6跳过、0失败（14.453秒）；工具专项与development30通过，包含读工具经正式action分页、调查拒写和幂等回执。
- Node全套346项：344通过、2跳过、0失败（51.508秒）；最后宿主无进展专项11项全部通过（含最终实质动作不误停修正）。脚本语法/diff检查通过，UI14项通过，机械设计检测[]。
- 新结构专项9项全部通过（包含真正extract.py脚本入口）；真实源只读副本 fresh infer+gate passed=true/8单元；正式本批extract+structure_gate均exit0，故障事件原检查verification=true。
- 扩展结构179项中另有1既有作文native warning测试失败，已以HEAD原structure_gate复现；本轮未放宽该无关逻辑。一次集成测试模块名test_source_anchors不存在导致loader error，修正选择后为上述467全绿。
- 未自动上传发布，不改已归档批次，不伪造完成。当前是原卡点解除并后续真实运行，非整份课时已完成。内置浏览器仍不可用，UI验收为真实render与状态测试，未声称视觉截图验证。

## 回退

按本次源码差异回退工具/等待/结构识别修复；保留失败候选、隔离测试与原回执，不能回退成虚假完成。现场启停状态须恢复。
