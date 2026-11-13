# 卡点：装配回填未共用版面投影，把网格题正确题干改回错位内容触发SOURCE_STEM_MISSING

- id: 2026-11-13-装配回填未共用版面投影,把网格题正确题干改回错位内容触发SOURCE_STEM_MISSING
- status: fixed
- batch: 七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun
- batch_dir: C:\Users\Administrator\.dsh\wf-plugin-data\workflows\七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun
- stage: assemble
- check: SOURCE_STEM_MISSING
- created: 2026-11-13T19:49:14+08:00
- last_seen: 2026-11-13T19:49:14+08:00
- occurrences: 1
- fixed_at: 2026-11-13T19:49:51+08:00
- fix_commit: 135686d
- verification: 真实批次 assemble → completed(deterministic, model_calls=0)，SOURCE_STEM_MISSING 与 BATCH_IDENTITY_MISMATCH 均消失；随后 validate 本地体检通过；test_ocr_grid 9 项 OK(含 GridAwareRestoreTest)，装配相关 9 套件 130 项 OK(skipped=4)。
- title: 装配回填未共用版面投影，把网格题正确题干改回错位内容触发SOURCE_STEM_MISSING

## 现象（卡点）

assemble 报 SOURCE_STEM_MISSING sec-f84d2b7afb671c5b-u24 第4题题干缺失或改写；随后重发布确认结构后又报 BATCH_IDENTITY_MISMATCH(缺 source_section.column_path)。

## 具体检查与判定

- 失败检查：SOURCE_STEM_MISSING
- 判定口径：①草稿/审计已按 OCR 版面块坐标把双栏网格题图文归位，但 restore_source_stems 仍用线性 _parts 回填，行优先顺序把第2/4/5格改成相邻题内容，第4格只剩一张图，审计判题干缺失——审计正确、回填在破坏已归位结果；②01_source/structure.confirmed.json 是旧代码发布的，缺当前 lesson_bands 产出的 column_path，身份逐字段比较因此不一致。
- 修复方向：①restore_source_stems 先按 ocr_grid.source_audit_items 建立网格回填项(即使线性解析没找到该题)，authoritative 项清掉线性错配选项；②审计侧 _grid_source_audit 用同一投影；③确认结构用确定性重算重发布(runner run --stage structure_gate --force)，不手改确认结构。

## 修复记录

- 修复方式：restore_source_stems 先取 ocr_grid.source_audit_items 并按网格建立回填项(即使线性解析没找到该题)，authoritative 项清掉线性错配选项；_grid_source_audit 用同一投影做来源审计；确认结构用 runner run --stage structure_gate --force 确定性重算重发布(带 source_section.column_path)，不手改确认结构、不放宽审计。
- 修复提交：135686d
- 复验证据：真实批次 assemble → completed(deterministic, model_calls=0)，SOURCE_STEM_MISSING 与 BATCH_IDENTITY_MISMATCH 均消失；随后 validate 本地体检通过；test_ocr_grid 9 项 OK(含 GridAwareRestoreTest)，装配相关 9 套件 130 项 OK(skipped=4)。
- 标记时间：2026-11-13T19:49:51+08:00
