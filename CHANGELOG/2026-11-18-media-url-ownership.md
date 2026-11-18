# 修复历史媒体 URL 导致的上传归属误报

## 背景
上传暂存阶段对 `batch_meta.json` 中的附件 URL 只接受当前批次目录前缀 `/source-media/<quoted-batch>/...`。已有批次在媒体部署或重组后可能保存内容寻址 URL（如 `/source-media/<hash>-a.png`）或批次内相对路径（如 `02_audio/a.mp3`）；这些 URL 虽非当前批次前缀，但附件文件仍可从当前批次目录唯一解析，导致错误 `Attachment URL is not owned by this batch`。

## 改动
- `app/workflow/media_inventory.py`：允许安全的批次相对媒体路径，以及带显式安全 `name` 的历史 `/source-media/...` URL；仍要求文件只能从当前批次媒体目录读取、同名文件唯一、后缀与 kind 匹配，外链、穿越、查询串、非法编码和缺失文件继续拒绝。
- `app/workflow/upload_payload.py`：媒体映射按安全 basename识别批次相对路径，交给现有 `media_map` 生成已部署 URL；不放宽外链、路径穿越或双重编码路径。
- 增加媒体库存和上传载荷回归测试。

## 验证
`workflow.test_media_inventory workflow.test_upload_payload workbench.test_media_upload_pipeline workflow.test_format_gate` 共 118 项通过。

## 边界
不修改用户批次数据，不自动部署媒体，不自动上传或发布。远端媒体可达性仍由实际 `prepare-upload/verify` 检查。

## 复现与根因确认
用合成批次复现：`batch_meta.json` 中图片 URL 为内容寻址 `/source-media/9f2a1b-image1.jpeg`（带 `name=image1.jpeg`）、音频 URL 为批次相对路径 `02_audio/seg-q-1.mp3`，两者本地文件都真实存在。旧代码在 `media_inventory.referenced_media` 直接以「是否以 `/source-media/<本批次>/` 开头」判归属，抛出 `MediaInventoryError: Attachment URL is not owned by this batch`；新代码同样输入可正常返回已部署映射，且不改写 `batch_meta.json` 一个字节。

## 安全边界（明确未放宽）
仍在拒绝：外链（`https://…`）、非媒体目录路径、路径穿越 `../`、双重/非法百分号编码、查询串与片段、`\` 分隔符、缺失文件、同名多目录歧义、后缀与 kind 不符。历史 URL 只有在**带显式安全 `name`** 且该文件在当前批次三个约定媒体目录中**唯一存在**时才回接，归属仍由本地文件与 SHA-256 证明。

## 补充验证
`workflow.test_media_inventory workflow.test_upload_payload workflow.test_upload workflow.test_upload_numbering workflow.test_format_gate workflow.test_batch_fill workbench.test_media_upload_pipeline` 共 208 项通过。
