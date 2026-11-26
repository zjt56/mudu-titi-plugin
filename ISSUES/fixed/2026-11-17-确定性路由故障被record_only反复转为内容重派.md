# 卡点：确定性路由故障被record_only反复转为内容重派

- id: 2026-11-17-确定性路由故障被record_only反复转为内容重派
- status: fixed
- batch: 【木牍教育】19年安徽中考英语-2
- batch_dir: workflows/【木牍教育】19年安徽中考英语-2
- stage: answers
- check: DETERMINISTIC_ROUTE_FAILURE_REQUEUED
- created: 2026-11-17T18:06:53+08:00
- last_seen: 2026-11-17T18:06:53+08:00
- occurrences: 1
- fixed_at: 2026-11-26T14:47:40+08:00
- fix_commit: 559fe96
- verification: 2026-11-26 复验 test_content_retry_scope+test_operations_development+test_recovery_runtime_failures 39 项通过（连续2轮，已隔离运维心跳）
- title: 确定性路由故障被record_only反复转为内容重派

## 现象（卡点）

2026-11-17 17:19:13至17:24:25，answers/knowledge各四次失败于Native agent requires selected provider/model route identity。self-heal.json四次plan.action=record_only却status=requeued；最终路由在独立修复后恢复，不能据后续retry_verified认定record_only修复有效。

## 具体检查与判定

- 失败检查：DETERMINISTIC_ROUTE_FAILURE_REQUEUED
- 判定口径：self_heal._apply把answers等阶段的record_only与upstream_rederive一起写反馈并重新排队；模型诊断已明确无内容错误、应修派发代码。失败原因相同但签名从b0ae8b8f881065aa变成0bb7e93d6ad24d5b；当前未证明变化来源，应避免将该变化直接认定无效。违反确定性失败不靠重复内容重派消化的判定。证据为workflow/self-heal.json及manifest.json阶段history。
- 修复方向：在现有自愈入口识别integration/config确定性故障，保留原候选和证据并升级代码修复，不转换为内容重生成；按失败语义及实际相关输入/代码绑定预算，补record_only不会重派同输入内容的回归。本次仅诊断，未修改执行器。

## 修复记录

- 修复方式：self_heal._apply 对 answers/types/explanations/knowledge/audio 的 record_only 直接拒绝(repair_not_applicable)并升级 code_repair，不写反馈不重派；路由/配置类 runtime 失败抛 content_runtime_requires_code_repair
- 修复提交：559fe96
- 复验证据：2026-11-26 复验 test_content_retry_scope+test_operations_development+test_recovery_runtime_failures 39 项通过（连续2轮，已隔离运维心跳）
- 标记时间：2026-11-26T14:47:40+08:00
