# 2026-11-09 修复多卷上传的题目 ID 冲突（question_id_exists）

## 背景 / 触发

2014 卷修复 `invalid_request` 并成功确认后，用户继续确认 2015 卷，题库返回
`409 {"error":"question_id_exists"}`，其他年份同样会卡住。

只读复现（生产库副本）：

- 2015 暂存 48 题，题目 ID 为 `q-1 … q-48`、`group-…`。
- 新确认的 2014 卷也使用 `q-1 …`，其中 **36 个 ID** 已存在于
  `question_units`，确认在写库前被 409 拒绝。
- 2015 十月那版云端记录使用 `AH-2015-001` 这类全卷前缀，而当前装配产物使用
  单卷内序号；两卷同时存在时必然相撞。

判定：**云端行为正确**（题目 ID 是全局主键，重复必须拒绝），问题在插件——
线上载荷直接复用了只在单卷内唯一的 `q-N` / `group-N`。

## 改了什么

1. `app/workflow/upload_payload.py`：`build_payload(..., id_prefix=None)`。
   传入前缀时，只给顶层题目 ID 加前缀；校验前缀只含字母/数字/点/下划线/连字符，
   非法前缀报 `ID_PREFIX_INVALID`；summary 记录实际前缀。显式 ID（如
   `AH-2014-001`、UUID）不会被改名。
2. `app/workflow/upload.py`：新增 `_auto_id_prefix`，在 `prepare` 阶段按批次自动生成
   前缀。仅当所有题目 ID 都是本地形态（`q-N` / `group-N-N`）时启用：
   `<year>-<sha256(batch_id)[:10]>`（无唯一确定年份时只用 hash）。前缀写入
   `plan.mapping_options`，`verify` 用同一前缀重放，冻结摘要保持确定性。
3. `app/workflow/test_upload_payload.py`：新增前缀命名、显式 ID 不改名、
   非法前缀阻断用例。
4. `app/workflow/test_upload.py`：新增 prepare 自动前缀、冻结后 verify 一致、
   显式 ID 不加前缀、同批次前缀稳定/跨批次不同用例。
5. `app/docs/upload-api.md`：写明本地 ID 的跨卷唯一性由上传前缀保证。
6. `CHANGELOG/RULES.md`：追加“题目 ID 全局唯一，跨卷必须加前缀”规则。

### 存量 2015 暂存的数据修复

2015 暂存已经写入云端，且本地已有 accepted 回执，工作台按设计禁止重复上传。
为让用户不必删回执/重建批次，按同一算法把该暂存载荷的 `id` 改成
`2015-fd3966e824-q-N`（只改 ID，不改题干/答案/附件/批次信息）：

- 改前备份：`data/backups/mudu.pre-id-prefix-20261109T025101Z.sqlite3`。
- 先在数据库副本上重放确认：**201**，创建 48 个单元，无 ID 冲突。
- 生产暂存已按同一脚本应用；碰撞检查为 0。

### 2016 / 2019 本地回执清理

这两卷本地 `receipt.json`/accepted attempt 指向的云端暂存已被删除（云端无该
staging，也无对应单元），工作台按设计会阻止重复上传。已把 `receipt.json` 与
accepted attempt 移到各卷 `07_upload/backups/stale-receipt-20261109T105231/`
（可原样恢复），`upload_state` 回到 `idle`；云端没有需要保留的旧暂存。

## 没改什么 / 边界

- 没有放宽云端 `question_id_exists`：重复 ID 仍拒绝，避免覆盖或制造重复题。
- 没有改用户批次内容、审核结论或发布状态；只修暂存载荷的题目 ID。
- 没有给显式 ID 的批次强行改名；已有 `AH-*` 等 ID 保持原样。
- 同一年份、同一批次目录再次上传仍会得到相同前缀并撞上自己；这是有意的防重，
  需要替换时应先删除云端旧草稿或使用新的批次身份。
- 没有自动确认：2015 仍需用户本人点「逐题确认」。

## 如何验证

- 修复前：生产库副本重放 2015 确认 → **409 `question_id_exists`**，
  36 个 ID 与已确认的 2014 单元冲突。
- 修复后：同一副本、前缀化后的 2015 载荷重放 → **201**，48 个单元。
- 真实批次构建（2014/2015/2016）：前缀分别为
  `2014-5052ff76ca`、`2015-fd3966e824`、`2016-7562edcecf`，跨年份 ID 交集为 **0**。
- `workflow.test_upload_payload workflow.test_wire_format workflow.test_upload`
  → **99 tests OK**。
- 上传相关套件（payload/wire/upload/upload_client/upload_content/delivery）
  → **184 tests OK**。
- 生产 `/api/health` 正常；暂存行已更新，备份文件存在。

## 回退

- 插件：回退 `upload_payload.py` / `upload.py` 的前缀改动与测试。
- 暂存：`data/backups/mudu.pre-id-prefix-20261109T025101Z.sqlite3`
  覆盖回 `data/mudu.sqlite3` 后重启服务（会同时回退这期间的其它暂存变更，
  仅在没有新确认时使用）。
