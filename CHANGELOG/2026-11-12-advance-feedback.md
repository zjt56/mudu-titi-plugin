# 2026-11-12 继续处理按钮回执与流程区反馈

## 背景

用户反馈流程右上角「继续处理」大多点击没有反应。检查发现旧前端忽略 POST 返回的 started/waiting/queued/reason/error；私有轮询以 running:false 判断结束，把排队当结束；错误写到上传表单区，流程区缺少明确回执。

## 改了什么

- `lib/client.js`：点击立即显示提交中；同批次在途请求去重。解析后台回执，在流程区按批次显示等待前卷、排队、已有执行器及拒绝原因，工作线程结束后的错误与停止原因也在本区显示。
- 回执立即合并到当前列表并刷新，随后复用已有统一进度轮询，移除额外600轮等待。15秒未收到回执或连接中断时只读 pipeline 对账，不自动重发 POST；无法确认时明确显示结果未知。
- `backend/advance-ui.test.cjs`：新增10项实际处理函数/回执回归，覆盖排队、拒绝、双击、网络断线、超时、卸载与工作线程错误。
- `app/docs/pipeline.md`：记录继续处理回执协议和反馈规则。

## 验证

- `node --test backend/advance-ui.test.cjs backend/operations-ui.test.cjs backend/upload-stage-flow.test.cjs`：45/45通过。
- `node --test backend/*.test.cjs`：277项，258通过、18失败、1跳过。失败集合与此前基线一致：8项生命周期、6项旧sh路由、3项operations-http、1项release-handoff；Windows缺少sh/python3入口及生命周期测试的win32进程模拟问题仍在，本次未改这些模块。首轮增加状态后使两项旧流程渲染测试失败，调整状态声明位置与恢复消息顺序后，上述45项及全量中的流程测试全部通过。
- `node --check lib/client.js`、`node --check lib/index.js`、`git diff --check`通过。
- git fetch已尝试，但GitHub TLS握手失败；未提交或推送。

## 边界及回退

本次只改前端请求和反馈，不改变后台串行顺序、执行锁、阶段状态、重试预算、审核差异及远端上传授权。不调用真实批次推进端点，不改用户批次。当前未验证GUI实际加载新前端，未发现运行中的dev:web构建监听，不能宣称已自动热更新；需客户端重新加载插件资源后使用。

回退时仅撤销本记录对应的继续处理状态/函数/流程提示、新测试和文档段落；保留工作区原有的表格、OCR及其他未提交修改。
