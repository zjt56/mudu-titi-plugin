# 2026-11-08 修复组级/子题图片附件在组装与沿用中被丢弃（上传报 SOURCE_IMAGE_MISSING）

## 背景 / 触发
23 年安徽卷点上传报：`原卷图片引用缺失或未映射到本单元共享材料：image10.jpeg`。
`question_content.audit_batch_source` 报 3 处 `SOURCE_IMAGE_MISSING`：
group-56-57-58（image10）、group-59-60-61-62（image15）、group-63-64-65（image16）。

## 根因（三处叠加，均导致组级/子题图片附件丢失）
1. `wire_format.normalize_item`（我此前为修 q-6 去重加的）：有 media_map 时**先清空旧 image/audio 附件再"从 [IMG] 文本重建"**。
   但 preassemble 已把 `[IMG]` 转成 attachments、文本里不再有 `[IMG]`，清空后重建不出来 → 组/子题图片整批丢失。
2. `batch_fill.initialize`：单元"未变"时直接 `output.append(previous[uid])` 沿用旧 batch.json 行（已丢图），
   从不采纳 draft（`question_draft`，权威）里的正确附件。
3. 由此 batch.json/base 缺组级图片 → 上传源审计拒绝。

## 修复
- `wire_format.normalize_item`：**保留原 attachments**（不再预清空 image/audio），从文本 [IMG]/audio_map 追加后统一去重；
  去重身份对 image/audio 用 `(kind,name)`（同一媒体多一份引用只留首次，通常是 preassemble 权威附件），避免既有附件与文本各存一份重复。
- `batch_fill.py` 新增 `_sync_media_attachments(kept,fresh)`：沿用未变单元时，把 draft 权威对象里存在、旧对象缺失的
  image/audio 附件按 kind+name 补入（不动题干/标签）。
  - finalized 且整体未变的分支：先补附件，如变化则同步 `finalized_bodies` 并 `_save`（避免正文冲突）。

## 验证（真实 23 卷）
- 修后重跑 assemble（确定性、model_calls=0）→ batch.json 三组附件齐（image10/15/16）。
- `invalidate meta` 重跑 meta→underlines→validate → `batch_meta.json` 三组附件齐；
  `question_content.audit_batch_source`：**passed=True，0 issues**（SOURCE_IMAGE_MISSING 消失）。
- 回归：`test_wire_format/test_upload_content` 50 通过、`test_upload` 23 通过（单独跑；组合跑时的
  `test_changed_api_base_requires_new_plan` 等为 mock 服务/端口环境抖动，隔离均 OK）。

## 边界
- 仅修复"附件丢失"的数据链路与去重口径；不放宽原卷保真、不改语义标签、不自动上传。
- 因 batch_meta 变化，review 需重跑（input_binding 含 batch_meta 哈希）；标签差异内容不受附件影响。
