# 卡点：unresolved 诊断行超出清理正文导致结构确认失败

- id: 2026-11-17-unresolved-诊断行超出清理正文导致结构确认失败
- status: open
- batch: 【木牍教育】16年安徽中考英语
- batch_dir: workflows/【木牍教育】16年安徽中考英语
- stage: structure_gate
- check: SOURCE_COORDINATE_MISMATCH
- created: 2026-11-17T18:14:58+08:00
- last_seen: 2026-11-17T18:14:58+08:00
- occurrences: 1
- title: unresolved 诊断行超出清理正文导致结构确认失败

## 现象（卡点）

SOURCE_COORDINATE_MISMATCH: line=332 outside raw body 1..278

## 具体检查与判定

- 失败检查：SOURCE_COORDINATE_MISMATCH
- 判定口径：structure.json 的 unresolved[QUESTION_NUMBER_INFERRED].line=332 来自含答案附录的原始全文；正文清理后只有278行；project_structure 把所有 line 当题目锚点投影
- 修复方向：denoise.project_structure 原样保留 unresolved 诊断、不参与坐标投影；真正题目锚点仍严格校验；批次16重跑 structure_gate 48单元通过

## 修复记录

（未修复）
