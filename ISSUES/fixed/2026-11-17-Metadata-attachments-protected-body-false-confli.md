# 卡点：Metadata attachments protected-body false conflict

- id: 2026-11-17-Metadata-attachments-protected-body-false-confli
- status: fixed
- batch: 七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun
- batch_dir: C:\Users\Administrator\.dsh\wf-plugin-data\workflows\七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun
- stage: meta
- check: protected_body_attachments
- created: 2026-11-17T10:41:37+08:00
- last_seen: 2026-11-17T10:41:37+08:00
- occurrences: 1
- fixed_at: 2026-11-17T10:47:19+08:00
- fix_commit: working-tree (batch_fill.py)
- verification: UTF-8 模式 attachments 回归 2/2 通过；目标批次只读保护字段复验 5 个顶层单元（含子题）通过，writes=0
- title: Metadata attachments protected-body false conflict

## 现象（卡点）

assemble/meta 阶段因 Metadata payload changes protected body field: attachments 失败；batch_meta.base.json 与 batch.json 的附件仅 URL 位置可能不同，消费者仍按原始字典比较

## 具体检查与判定

- 失败检查：protected_body_attachments
- 判定口径：metadata 回填保护检查未复用现有 canonical attachment URL 等价口径，确定性误报正文变化
- 修复方向：在 _overlay_item_metadata 的 attachments 比较中复用 canonical body 归一化，补回归测试并复验真实批次

## 修复记录

- 修复方式：metadata 保护检查对 attachments 复用 canonical body 归一化，仅忽略传输 URL，保留名称、类型、placement、数量等身份保护
- 修复提交：working-tree (batch_fill.py)
- 复验证据：UTF-8 模式 attachments 回归 2/2 通过；目标批次只读保护字段复验 5 个顶层单元（含子题）通过，writes=0
- 标记时间：2026-11-17T10:47:19+08:00
