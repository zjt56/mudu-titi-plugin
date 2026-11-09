# 2026-11-09 插件上传规则对齐服务器契约

## 背景

用户要求「插件端修改规则匹配服务器要求」。延续附件位置与跨卷 ID 修复，核查服务器 normalize_attachments 实现（每题最多 20 个附件）及题目全局 ID 约束。

## 改了什么

- app/workflow/upload.py：只要批次中存在本地 q-N/group-N 就生成批次前缀，修复混入自定义 ID 后整批跳过前缀的问题。
- app/workflow/upload_payload.py：前缀逐题应用，仅改本地 q-N/group-N，保留自定义 ID；每层附件超过 20 个报 ATTACHMENTS_LIMIT_EXCEEDED，不截断媒体。
- app/workflow/test_upload.py、test_upload_payload.py：新增混合 ID 的 prepare→freeze→verify、不修改原批次、自定义 ID 保留及 20/21 附件边界回归。
- app/docs/upload-api.md：同步混合 ID 与附件数量约束。

## 边界

只修改插件上传副本映射与校验，不修改云端代码、数据库、用户批次、已冻结载荷或回执；不提交、确认或发布题目。原附件位置四值白名单、URL、选项绑定、知识点及冻结校验保留。

## 验证

从 app 运行 PYTHONPATH=. python3 -m unittest workflow.test_upload_payload workflow.test_wire_format workflow.test_upload workflow.test_upload_client workflow.test_upload_content workflow.test_delivery：187 项全部通过。

## 回退

回退本条记录对应的 upload.py、upload_payload.py、测试和文档改动即可；不需要恢复用户数据。
