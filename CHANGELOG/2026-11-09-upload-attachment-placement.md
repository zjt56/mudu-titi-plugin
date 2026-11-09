# 2026-11-09 修复题库「逐题确认」报 invalid_request（附件 placement=material）

## 背景 / 触发

用户在题库服务器「上传暂存」里点「逐题确认」，页面只显示 `invalid_request`，
2014 年安徽卷整批 52 题无法转入审核/草稿。

排查（只读复现，未确认生产暂存）：

- 服务端日志：`POST /api/uploads/{id}/confirm` 返回 400，未写业务日志。
- 用同一份 staging 载荷在数据库副本上重放确认流程，捕获到
  `ValueError: invalid_attachment`（`backend.py::normalize_attachments`）。
- 载荷里 `group-91-92-93-94-95`（阅读理解）的配图使用
  `"placement": "material"`。题库 API 只接受
  `question / option / answer / explanation`（`developer.md` 与
  `static/app.js` 的 `mediaMarkup` 都只有这四个位置），因此确认阶段
  整批被 400 拒绝；上传暂存阶段不校验附件，所以问题直到逐题确认才暴露。

根因在插件侧：`wire_format.py` / `upload_payload.py` 把正文材料里的 `[IMG]`
转成 `placement="material"`，并允许显式 `material`/`stem` 位置直接进入线上载荷。

## 改了什么

### 插件（本仓库）

1. `app/workflow/wire_format.py`：材料里的 `[IMG]` 改为
   `placement="question"`（材料图随题干显示）。
2. `app/workflow/upload_payload.py`：
   - 材料 `[IMG]` 同样生成 `question`；
   - 历史批次/装配产物里显式的 `material`、`stem` 位置在生成线上载荷时
     归一为 `question`，不再把非法位置发给题库；
   - 其余未知位置继续报 `ATTACHMENT_PLACEMENT_INVALID`，没有放宽附件校验。
3. `app/workflow/test_upload_payload.py`：更新 media_map 用例的期望值，
   新增 `test_legacy_material_and_stem_placements_normalized_for_question_bank`
   回归（material/stem 必须变成 question）。
4. `app/workflow/test_wire_format.py`：新增 `test_material_image_uses_question_placement`，
   锁住装配阶段材料图不能生成 `material`。
5. `app/docs/upload-api.md`：写明题库只接受四个 `placement`，材料/题干图片统一按
   `question` 发送。
6. `CHANGELOG/RULES.md`：追加规则条目，`material`/`stem` 不得直接上线。

### 题库服务器（101.43.1.57，非本仓库）

1. `backend.py::normalize_attachments`：把 `material`/`stem` 作为
   `question` 的向后兼容别名，避免已经写入暂存的旧载荷在逐题确认时整批 400。
   这是容错读取，不改变附件 URL/类型/选项绑定校验。
2. `backend.py::do_POST` 的 `ValueError` 分支增加
   `print("POST value error", repr(exc))`；线上错误码仍是 `invalid_request`，
   但服务日志能直接看到具体原因（本次即 `invalid_attachment`）。
3. 改动前已备份：`/home/ubuntu/mudu-quiz/backend.py.bak-20261109T022317Z`。

## 没改什么 / 边界

- 不代为点「逐题确认」：确认仍是人工动作；本次只修到让该动作不再 400。
- 没有修改该批次已写入的暂存载荷，也没有生成草稿、审核批次或发布。
- 没有放宽附件 URL、类型、选项字母、数量、知识点等校验。
- `material` 图片没有独立渲染位；题目卡片只渲染四个位置，材料图会显示在题干
  位置，这是题库现有展示能力的边界。

## 如何验证

- 复现：数据库副本重放原 staging 确认流程 → 400 `invalid_request`，
  异常栈定位 `normalize_attachments` 的 `invalid_attachment`。
- 修复后同一副本、同一载荷重放确认 → **201**，创建 52 个单元
  （`q-1 … q-101`、`group-16-17 … group-91-92-93-94-95`），
  生成审核批次 `RB-88F47842A88F4F`。
- 生产服务器已备份、打补丁、`py_compile` 通过并重启；
  `/api/health` 返回 `{"ok":true,"api_version":"2026-09-29"}`。
- `PYTHONPATH=. python3 -m unittest workflow.test_upload_payload workflow.test_wire_format`
  → **71 tests OK**。
- `PYTHONPATH=. python3 -m unittest workflow.test_upload_payload workflow.test_wire_format
  workflow.test_upload workflow.test_upload_client workflow.test_upload_content workflow.test_delivery`
  → **179 tests OK**。

## 回退

- 插件：回退上述两个 Python 文件的 placement 改动与测试。
- 服务器：`cp -p backend.py.bak-20261109T022317Z backend.py && sudo systemctl restart mudu-quiz`。
