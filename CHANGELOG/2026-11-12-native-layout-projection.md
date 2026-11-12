# 原生来源对象与布局投影校验

日期：2026-11-12

## 背景

用户授权按来源对象、结构归属与消费链路正确性的方向优化上传流程。原生提取已有合并网格，但后续表格仍存在序列化文本逆向拆列、空行丢失及媒体跨位置错误去重风险；仅验证上传 schema 不能证明单元格内容仍属于原位置。

## 改了什么

- `app/workflow/source_objects.py`：新增 `native-source-objects/v1` 索引，绑定原件摘要、包内部件、遍历路径、对象内容与父对象；区分同文件图片的多次出现。表格兼容入口使用明确编号和完整文字，再验证原生引用；坏索引不回退为已证明来源。
- `docx_extract.py`、`question_draft.py`：提取附加索引，组装从原生网格投影并保留来源引用；解析代码摘要纳入候选失效依据。可用旧来源保持兼容，新来源证据失效如实报告。
- `app/workbench/server.py`、`lib/client.js`：原卷表格预览保留合并跨度、首尾省略列、全空行及同格图片，嵌套标记不截断外围表格。只读回退不授予原生布局证明。
- `wire_format.py`：按媒体文件及 placement/optionKey 去重，保留同图不同选项、答案、解析归属，同位置重复引用幂等。
- `upload_payload.py`、`format_gate.py`：最终适配重新读取当前来源；逐单元核对表格逐格文字、顺序与 rowspan/colspan。漏表、换格、改合并拒绝。平台契约暂不能表达的单元格图片和嵌套表格有独立能力错误，不静默扁平化为成功。
- `phonetic_structure.py`：无适用单元时不重建整份候选；坏来源证明返回无豁免，不因额外来源校验而崩溃。
- 新增四组回归，包含真实函数组成的合成 DOCX → draft → source_blocks → build_payload 正/负链路。

## 边界

保留现有18阶段与12类展示、听力并行、Word内容权威、PDF整卷OCR与审核差异规则。早期内容体检不新增平台格式检查；适配能力错误在最终上传构建中处理。来源索引不发送远端。不重跑真实/归档批次、不修改用户试卷、不调用模型/OCR或自动提交上传。

这不是全量来源归属 IR 迁移：表格入口仍兼容完整文字定位；跨所有平台消费者的实际浏览器渲染、单元格媒体协议扩展及全卷视觉比对尚未完成。

## 验证

- 新增专项最终：37/37 通过（`test_source_objects` 17、`test_source_table_preview` 7、`test_media_occurrence_projection` 9、`test_phonetic_proof_errors` 4）。空白/数字空档归一化等价；字母画线丢失或移到别的字母/单元格仍判差异。
- 扩展定向：253 项，1 项测试内2个失败子例，均为既有 `workbench.test_media_scoped_preview` 对 scoped 预览是否包含同节导语的预期。实际 `_scoped_source_slice` 按已有 `phonetic_section_rubric` 补入当前标题，但旧测试断言只有 IMG；该测试、`preview.py`、`shared_rubric.py` 均未修改，`git diff --exit-code` 确认。较早错误测试模块名的轮次不计为通过。
- `node --check lib/client.js` 与 `lib/index.js` 均通过。
- 前端全量：267 项，248 通过、18 失败、1 跳过。只读核查18项前置原因：6项旧路由测试固定 `sh`，本机缺失得到 ENOENT；5项测试调用 `python3` 命中 WindowsApps 别名，退出9009，可用 `python` 为3.12.10；其余7项生命周期 fake 使用真实 win32，但 taskkill 空mock不触发fake child的exit。未修改生产进程管理或测试来掩盖失败，不宣称全量通过。
- Python全量已尝试，运行约26分钟仍未结束，发现子进程调用实际 `backend/wf-router.mjs` 等待模型路由，期间有失败/错误输出；已取消本轮自建测试任务，记为未完成，不宣称通过。未触碰其他流程的锁或进程。
- 最终扩展定向：250/250通过（独立 TEMP/TMP，覆盖 source_objects、source_table_preview、media_occurrence_projection、phonetic_proof_errors、docx_extract、question_draft、wire_format、upload_payload、format_gate、phonetic_structure、native_unnumbered_structure、preview_labels、writing_preview、question_identity_preview、preassembly）。
- `git diff --check` 通过。

测试均使用临时合成批次。真实平台显示效果未经本次端到端运行验收。

## 回退

撤销本记录列出的本轮局部代码/测试及文档改动，保留工作区其他既有修改。无用户数据迁移需要回滚；新索引为提取附加字段，不清理既有来源证据。不得用整文件恢复覆盖他人同期改动。
