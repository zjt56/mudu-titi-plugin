# 卡点：知识点局部重生成后派生批次仍为旧标签，最终格式检查报幻影 KNOWLEDGE_REQUIRED

- id: 2026-11-12-知识点局部重生成后派生批次仍为旧标签,最终格式检查报幻影-KNOWLEDGE_REQUIRED
- status: fixed
- batch: 05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec
- batch_dir: workflows/05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec
- stage: format_gate
- check: KNOWLEDGE_REQUIRED
- created: 2026-11-12T12:10:38+08:00
- last_seen: 2026-11-12T12:10:38+08:00
- occurrences: 1
- fixed_at: 2026-11-12T12:41:56+08:00
- fix_commit: 802499b
- verification: 真实批次 07_review/upload-format.json status=passed、errors=[]、final_sha256 与当前 batch_meta 一致；test_stage_semantics 29 通过、test_review_rounds 90 通过、test_format_gate 23 通过、test_runner_auto 46 通过
- title: 知识点局部重生成后派生批次仍为旧标签，最终格式检查报幻影 KNOWLEDGE_REQUIRED

## 现象（卡点）

knowledge 阶段重跑改写 04_knowledge/knowledge.json 后 assemble 已更新 06_batch/batch.json，但 meta/underlines/validate 未随上游真实内容变化失效，06_batch/batch_meta.json 仍是旧标签（u7 组级=名词辨析、1/2/3 小题 knowledge=[]）。format_gate 读取旧 batch_meta 判 u7/1-3 knowledge 非空失败，与当前 batch.json/知识标签不一致，属派生视图过期而非题目数据缺失。

## 具体检查与判定

- 失败检查：KNOWLEDGE_REQUIRED
- 判定口径：knowledge 阶段重跑改写 04_knowledge/knowledge.json 后 assemble 已更新 06_batch/batch.json，但 meta/underlines/validate 未随上游真实内容变化失效，06_batch/batch_meta.json 仍是旧标签（u7 组级=名词辨析、1/2/3 小题 knowledge=[]）。format_gate 读取旧 batch_meta 判 u7/1-3 knowledge 非空失败，与当前 batch.json/知识标签不一致，属派生视图过期而非题目数据缺失。
- 修复方向：is_stale 对 structure_gate/meta/underlines/validate/review_gate/format_gate 比较已记录的 artifact 上游输入摘要，真实内容变化即重算（等价 JSON 重写不触发）；重跑 meta→underlines→validate 后 format_gate 无错误；同时修正二审 sample 提示：TARGETS 带 number 的组题小题必须原样写回 number，只有 number=null 才整组核对。

## 修复记录

- 修复方式：is_stale 对 structure_gate/meta/underlines/validate/review_gate/format_gate 比较已记录的上游 artifact 输入摘要，真实内容变化即失效重算（等价 JSON 重写不触发）；本批次重跑 meta→underlines→validate 后 format_gate 通过；同时补二审组题小题 number 契约，避免 sample 漏覆盖。
- 修复提交：802499b
- 复验证据：真实批次 07_review/upload-format.json status=passed、errors=[]、final_sha256 与当前 batch_meta 一致；test_stage_semantics 29 通过、test_review_rounds 90 通过、test_format_gate 23 通过、test_runner_auto 46 通过
- 标记时间：2026-11-12T12:41:56+08:00
