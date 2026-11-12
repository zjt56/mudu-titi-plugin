# 2026-11-12 最终来源审计和语音导语共用图片板块证据

## 背景

06七上人教版课时A计划StarterUnit2KeepTidy!第二课时Pro结构闸门正式通过后，组装审计出现5个BATCH_IDENTITY_MISMATCH及B横幅被误算到语音题D选项的SOURCE_IMAGE_MISSING；随后语音组因原始导语未恢复而触发A8。前一轮只验证结构/草稿/payload而漏掉最终audit_batch_source与shared_rubric的独立重建，是本轮修复遗漏。

## 修改

- question_content._rebuild_source_units：来源审计、保真恢复、写作原题干恢复共用当前lesson_bands.for_text验证后的sections，再按结构闸门相同方式规范source_section。不相信批次自身band宣称，不放宽身份检查。
- shared_rubric.phonetic_section_rubric接收调用方独立验证的band_evidence，独立标题候选经同源截窗后比较；question_draft、preview、最终审计均从当前原件/正文/媒体取得证据，不用unit自身字段当证明。原严格标题、全题号、完整窗口检查保留。
- 新回归覆盖真实类型的语音ABCD组紧邻B图片横幅，正式gate→draft导语→audit_batch_source→phonetic来源证明→原卷预览；篡改band标签或媒体字节仍拒绝。

## 边界

不改题型/答案/知识点，不删除身份/图片/语音形态校验。原始Word/正文不变。指定批次经正常runner/batch_fill刷新源字段并续跑，不手改manifest，不自动上传发布。

## 验证

- Python3.12、独立TMPDIR，9模块207 tests，OK，4 skipped（2.192s）。最初命令误写不存在的test_shared_rubric模块导致loader错误，已纠正模块列表复跑。
- 06真实batch.base和batch.json的audit_batch_source均passed、issues=[]，5个unit全部checked。
- 当前语音组原导语恢复，phonetic_structure.shape=(True,True)，其来源证明含4个语音组。
- 导入前恢复选项采用key/text对象，而draft/wire为带标签字符串；phonetic_structure逐项转成(label,text)比较，仅忽略标签分隔排版，保留标签顺序/文字/画线/题号/身份严格校验，解决实际导入A8/A9误报。真实assemble已正式通过，model_calls=0。
- 继续发现画线映射把短词选项we/old/put误与其它文章weather/years old/computer竞争。mark_underline保留显式选项上下文角色，只比较完整选项；重复完整选项仍歧义，原文章上下文/镜像边界不放宽。
- 06正式runner最终到validate：19个体检项目全部通过，0阻断、0提示、0跳过。underlines passed，changes=[]、issues=[]；原始正确画线不改写。内容独立审核尚未运行，未上传发布。
- 中间新增CLI预检测试因合成样本answer空产生A9，补齐测试夹具答案后重新执行；不修改生产检查来通过测试。

## 回退

回退本轮question_content/shared_rubric及3个调用方的hunk和新增测试；保留原始来源与所有真实失败日志。
