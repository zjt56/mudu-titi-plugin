# 2026-11-08 简化哈希/过期校验：流程像“记录数据”一样逐步填充，内容正确性关卡保留

## 背景 / 触发

用户反馈流程反复卡顿。根因之一是**过度校验**：每个交接点用“消费者完整校验器”
反复比对生产者输入哈希、逐文件内容指纹，任何细微变化（如 pdf-ocr.json 抖动）
就让已完成的下游被判失效、重跑，流程卡住。用户明确：原卷一次提取好后基本不再改，
后续只是逐个补缺字段（答案/解析/知识点/难度），**内容正确性由用户后续自己验证**；
只需要“数据一步步往下填”，不要反复哈希比对卡流程。

## 改了什么

1. **`manifest.is_stale` 大幅简化**（`app/workflow/manifest.py`）：只判断
   “阶段完成 + 产物文件齐全 + （上传闸门另需）批准哈希一致”，不再逐文件重算
   SHA256、不再比对输入/输出语义指纹。
2. **`handoff.inspect_stage` 去掉“生产者输入哈希变化即拒绝交付”**（`app/workflow/handoff.py`）：
   不再因为某个输入文件的哈希变了就把交付判无效。
3. **`manifest.record_semantics` 只记普通哈希**，不再计算逐单元语义签名。
4. `_content_digest`：JSON 按规范化结构哈希（忽略时间戳/词数/难度等易变字段），
   仅用于残留的廉价比较。

## 明确保留（内容正确性，绝不能错 —— 用户强调）

- **`dependency_satisfied` 仍调用 `inspect_stage`**：下游消费上游前，仍用消费者校验器
  确认**题号覆盖、答案对应到正确题号、结构绑定、组装格式、选项条数**等正确性。
- **`validate` / `format_gate` / 上传前** 仍跑完整体检（`checks.run_all`）：
  结构、答案、正文保真、题型版面、原卷逐题覆盖等全部 blocking。
- 未完成 / needs_review / 重建中的交付仍不可被下游消费（质量≠执行）。

## 没改什么 / 边界

- 不自动上传/发布；不动用户批次数据。
- “内容变化后是否重算”不再自动判定，交由用户后续验证或显式 invalidate。

## 如何验证

- 23 卷实数据：`dependency_satisfied` 对 answers/types/audio/explanations/knowledge/
  assemble/meta/underlines 全部 True（此前因 `pdf-ocr.json` 哈希抖动被判 False 卡住）；
  全阶段判定 0.5s 内。
- 23 卷跑 `do_validate`：结构/答案/正文保真/题型版面/原卷覆盖 全部 passed。
- `workflow.test_stage_semantics`(27) + `test_agent_protocol`(29) + `test_explanations_direct`(4)
  共 60 项通过。

## 回退

还原 `manifest.is_stale` / `handoff` 的输入绑定段 / `record_semantics` 即可。

## 追加（2026-11-08）：过期状态不再阻碍上传

用户要求“过期/内容变化状态不要阻碍批次上传”。据此调整上传前检查
（`workbench/server.py::_check_upload_preconditions`）：

- 去掉 `is_stale(validate)` 门槛：不再因为 validate 产物内容变化/过期就拒绝上传；
  只要 `validate` 曾 `completed` 即放行。
- 保留其它“内容对不对”的硬校验：批次标签校验、试卷结构校验、审核状态确认
  （有审核差异时仍需用户在界面确认上传，带 attempt_token 通过）。
- 内容正确性由用户自行把关；本改动不自动上传远端、不改产物。

验证：23 卷（validate=completed 但 is_stale=True）上传前检查不再报“已过期”；
带确认 token 时通过（仅剩审核差异的正常确认）。

## 追加2（2026-11-08）：未完成审核/审核差异也不阻碍上传

用户再次强调（此前已强调过，本次补记入规则）：**未完成审核也不影响批次上传到题库服务器**。
据此改 `workbench/server.py::_check_upload_preconditions`：

- 去掉 `elif not summary.get('passed'): raise '该批次尚未完成审核'` 硬拦截；
  未完成审核/有审核差异时照常允许上传。
- 仅在“已带确认令牌但审核 revision 变了”时提示重新确认（防误传旧版本），不硬挡。
- 保留内容硬校验：validate 需曾 completed、批次标签、试卷结构。

已同步写入根 `AGENTS.md` 硬规则 3 与 `CHANGELOG/RULES.md`（2026-11-08 条）。
验证：23 卷（review_gate=needs_review、审核未 passed）不带 token 上传前检查通过；
真实 revision+token 也通过。
