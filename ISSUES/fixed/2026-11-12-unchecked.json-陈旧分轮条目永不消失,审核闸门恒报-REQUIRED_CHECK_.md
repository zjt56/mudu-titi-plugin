# 卡点：unchecked.json 陈旧分轮条目永不消失，审核闸门恒报 REQUIRED_CHECK_UNCHECKED

- id: 2026-11-12-unchecked.json-陈旧分轮条目永不消失,审核闸门恒报-REQUIRED_CHECK_
- status: fixed
- batch: 07七上人教版课时A计划StarterUnit2KeepTidy!第三课时Sec
- batch_dir: C:\Users\Administrator\.dsh\wf-plugin-data\workflows\07七上人教版课时A计划StarterUnit2KeepTidy!第三课时Sec
- stage: review_gate
- check: REQUIRED_CHECK_UNCHECKED
- created: 2026-11-12T19:22:20+08:00
- last_seen: 2026-11-12T19:22:20+08:00
- occurrences: 1
- fixed_at: 2026-11-12T19:23:01+08:00
- fix_commit: d9fe33d
- verification: 批次 07 复跑 review_gate：reconciled_unchecked=2、unchecked.json=[]、required_unchecked=0，仅剩 15 项人工差异
- title: unchecked.json 陈旧分轮条目永不消失，审核闸门恒报 REQUIRED_CHECK_UNCHECKED

## 现象（卡点）

review-gate.json errors: REQUIRED_CHECK_UNCHECKED ×2（来自 07_review/unchecked.json 18:55 写入的 REVIEW_SAMPLE_SOURCE_UNVERIFIED 行），即使 review 已复验通过也无法消除

## 具体检查与判定

- 失败检查：REQUIRED_CHECK_UNCHECKED
- 判定口径：ingest.py:554-558 导入审核结果时把 old_unchecked 无条件 preserved 并拼接新结果，旧行永不出清；闸门读 unchecked.json 即视为必检未过
- 修复方向：新增 review.reconcile_unchecked：以 evaluate_rounds 为权威，execution_completed 为真时只丢弃「code 属 ROUND_UNCHECKED_CODES 且 (unit_id, field) 不在当前 unchecked」的陈旧行，并在 do_review_gate 开头执行、把被丢弃行记入 reconciled_unchecked

## 修复记录

- 修复方式：新增 review.reconcile_unchecked，以 evaluate_rounds 为权威清理陈旧分轮行；do_review_gate 开头执行并记录 reconciled_unchecked
- 修复提交：d9fe33d
- 复验证据：批次 07 复跑 review_gate：reconciled_unchecked=2、unchecked.json=[]、required_unchecked=0，仅剩 15 项人工差异
- 标记时间：2026-11-12T19:23:01+08:00
