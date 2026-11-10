# 2026-11-10 重复上传不再“批次任务消失”：明确提示已复用批次

## 背景

用户反馈：点击「上传试卷」后，批次任务从列表里消失了。排查确认，重复上传同一份已上传到云端的试卷时，去重逻辑会复用已有批次；该批次 `uploaded=true`，被队列和审核列表同时过滤掉，前端也没有任何提示，于是看起来“批次任务不见了”。

复现：对 `16年/19年/2024` 三个 `uploaded=True` 的批次，用同一份内容调用去重，均返回其自身（`_existing_upload_batch` 命中），随后列表不可见。

## 改了什么

- `app/workbench/server.py::handle_upload`：复用已有批次时返回 `uploaded` 标记和 `message`。已上传返回「该试卷已上传完成，无需重复上传（批次：…）」；未上传返回「该试卷已存在，已定位到已有任务（批次：…）」。`overview` 取不到时按 upload 阶段状态兜底。
- `lib/client.js::UploadView`：新增 `notice` 状态，提交成功且为重复复用时展示提示；已上传的重复批次不再强行选中（避免指向看不见的批次）。
- `app/workbench/test_upload_status.py`：新增重复上传回归，断言 `duplicate/uploaded/message`。

## 没改什么 / 边界

不改变去重规则（同内容仍复用，避免生成 -2/-3 副本与重复跑模型）；不自动放行、不上传；非重复上传行为不变。

## 验证

- `workbench.test_upload_status` 9 项通过（含重复上传提示）。
- `workbench.test_upload_status`、`test_list_projection`、`test_stage_parallel_display`、`workflow.test_workbench_review_status`、`test_workbench_catalog_archive`、`test_upload_content`、`test_upload_payload` 共 166 项通过。
- `node --check lib/client.js` 通过。

## 回退方式

回退本提交即可；无数据结构变化。
