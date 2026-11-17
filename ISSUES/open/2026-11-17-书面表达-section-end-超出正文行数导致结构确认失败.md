# 卡点：书面表达 section end 超出正文行数导致结构确认失败

- id: 2026-11-17-书面表达-section-end-超出正文行数导致结构确认失败
- status: open
- batch: 【木牍教育】19年安徽中考英语-2
- batch_dir: workflows/【木牍教育】19年安徽中考英语-2
- stage: structure_gate
- check: SOURCE_COORDINATE_MISMATCH
- created: 2026-11-17T17:16:59+08:00
- last_seen: 2026-11-17T17:16:59+08:00
- occurrences: 1
- title: 书面表达 section end 超出正文行数导致结构确认失败

## 现象（卡点）

SOURCE_COORDINATE_MISMATCH: source_end_line=319 outside raw body 1..315

## 具体检查与判定

- 失败检查：SOURCE_COORDINATE_MISMATCH
- 判定口径：structure.json 把Ⅺ.书面表达 end 记为319，清理后正文315行；319之后是答案附录标题，不是作文正文；project_structure 只接受 len+1 终止边界
- 修复方向：project_structure 仅对书面表达 section 将超过正文行数的 end 夹到最后一行；普通大题/起始坐标/删除行锚点仍严格报错；批次19-2重跑 structure_gate 48单元通过

## 修复记录

（未修复）
