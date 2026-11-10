# 2026-11-10 2024 卷解析图标标题漏识别与直抄交付修复

## 背景、触发与批次

用户反馈 `【木牍教育】2024中考英语试卷` 在 validate 的 `解析 check_explanations` 失败，报 ORIGINAL_EXPLANATION_OMITTED。

批次目录：`workflows/【木牍教育】2024中考英语试卷`。严格来源检查实际定位到 50 条原解析和 8 条原文章大意被占位「无」替代，均已有真实 Word 原文，并非需要模型生成。

## 根因

1. Word 附录以 `[IMG image14.jpeg]解析[IMG image15.jpeg]` / 图片夹住“文章大意”文字作为标题。直抄快路仅识别 `【解析】` / `【文章大意】`，漏掉内容。严格消费者已能识别图标标题，因此最终体检正确拒绝了遗漏。
2. 直抄快路直接写正式 explanations.json 并标记完成；content_rules 又因 evidence 包含“按题号从 Word 原文直接抄录”而跳过来源检查。说明文字被误当成校验凭据，遗漏未在生产者阶段拦住。
3. 大意曾通过 zip 按顺序分配；一篇缺失或 Word/PDF 混合时存在串篇风险。
4. 即使恢复了解析生产者和 batch.json，旧 batch_meta.base.json / batch_meta.json 的结构仍合法，调度只比较它们自己的输出，未发现没有同步新解析，导致再次检查仍报同一错误。

## 改了什么

- explanations_direct：复用 upload_content 的来源标题/边界提取，识别图标标题，保留多行原文与来源行号；按完整题号集合绑定同篇大意，不按位置分配。Word 逐字段优先，只给相同题/篇章的缺失字段使用 PDF/OCR 兜底。权威内容冲突、摘要归属不唯一不选择首条。
- install/agent 入口：直抄也走 export_task 输入绑定 → 提取候选 → import_result 通用校验/安装/交付，来源变化拒绝安装；不直接写正式文件、不单独 mark 完成。此流程不调用模型。
- content_rules：删除直抄证据字符串跳过来源审计的路径，缺失占位仍须满足原文确实缺失；保留书面表达既有例外。
- handoff / manifest.is_stale：组装、meta、underlines 的内容交付须保留当前已接受的解析/大意及子题解析；不一致时仅让真实失配消费者失效并重建，不因 JSON 排版差异重跑上游。
- 文档与解析员角色：明确图标标题仍是原文标题、按题号/篇章绑定、直抄不能绕过校验。

## 现场恢复与边界

通过正常 runner/protocol 重提取原文解析，随后重建受影响的 meta、underlines，重新运行 validate。未修改 manifest 造状态，未生成解析，未重判题型/答案/知识点，未重跑提取和听力，未改审核差异或上传授权。

验证同一批次：

- `06_batch/batch.json` / `batch_meta.base.json` / `batch_meta.json` 三层 strict 来源检查全部通过，errors=0。
- explanations/assemble/meta/underlines/validate 五个交付消费者检查全部通过。
- validate：19 项成功，阻断 0、提示 0、跳过 0；解析检查可溯源 58 条、AI 内容 0、未溯源 0、违规 0。
- 后端 advance 回执 `ok=true, started=false, running=true`：已在正常串行队列续跑，无重复派发。随后复核 review=completed、format_gate=completed/passed=true/errors=0，local_complete=true，诊断 blocking_count=0。12 项审核差异完整保留，review_gate=needs_review/release_ready=false，未提交远端。

## 回归验证

- 提取/绑定导入/来源/下游交付等相关回归 207 项通过（后续全量覆盖新增 is_stale 内容同步路径）。
- 新用例覆盖图标标题、多行、错题绑定、缺篇不顺移、Word/PDF 同字段优先、冲突不挑首条、说明字符串不能豁免遗漏、绑定后来源改变拒绝安装、旧元数据投影须重建。
- 常规全量测试因正式审核持有机器共享锁而等待，已取消；改在独立临时目录隔离测试锁重跑。两条依赖 ps 的进程探活用例因环境权限明确跳过，不绕过限制。
- 全量 workflow 最终结果：2014 项，1995 项通过、19 项跳过（含原有17项及上述2项权限限制），失败/错误 0；耗时87.296秒。
- 全量期间发现并修正的回归：上游修复中不提前重查后代；无法解析的旧上下文不能冒充“已证明的内容变化”，来源格式错误仍由严格交付校验拒绝，保留原故障归属。调度/恢复/变更记录等定向复验60项通过。
- git diff --check 通过。
- 拉取远端受网络影响：HTTPS fetch HTTP2 失败，SSH fetch 连接未完成后取消；没有强推或覆盖远端。

## 回退

回退本提交恢复旧快路；不建议回退到绕过来源检查的旧路径。批次产物经通用导入器及原检查器真实验收，不以代码回退伪造其状态。
