# 2026-11-12 同步补全对话行内五空复用共用锚点

## 背景（触发与授权范围）

主代理在八上01隔离提取件发现：无 Roman 的「补全对话」正文 `Amy:Hello,Bill.1.<u>　　　　</u>`、`Amy:…2.[IMG image3.jpeg]` 等五个真实空位未进入 `extract.infer_structure`，产生 `QUESTION_BOUNDARY_MISSING`。主代理明确授权本次极小提取分支与共用预览锚点修改；不修改已有 PDF OCR hunk。

## 改了什么

- `app/workflow/extract.py`：只在同步练习、已判 group 且属于补全对话/填空类的既有题号与 blank_numbers 均未命中时，调用共用 `preview._weak_numbers(raw)`。位置由原循环写入，保留原文，不读答案、不推断连续题号。
- `app/workflow/preview.py::_weak_numbers`：说话人前缀明确、行内数字紧邻 `[IMG ...]` 时识别可见空位号，供提取、结构闸门与身份锚点共用；普通图编号、只有图无号、普通句号数字均不新增空位。
- 新增 `app/workflow/test_extract_sync_dialogue_blanks.py`：真实五空字符串、图片空位、共用结构锚点、答案不得补正文缺号、非同步卷不扩展、普通数字/图标不误判。

## 验证

- 只读调用真实隔离正文的 `infer_structure`：补全对话标题行24，`questions=[1,2,3,4,5]`，`question_locations` 为25、27、29、32、35，`QUESTION_BOUNDARY_MISSING=[]`；未写回该目录。
- 11个答案/来源/新提取模块：**137 tests / 4.642s / OK**。
- `WF_STRUCTURE_AI=0 PYTHONPATH=app python3 -m unittest workflow.test_extract_sync_dialogue_blanks workflow.test_extract workflow.test_preview_labels workflow.test_question_identity_preview workflow.test_structure_gate`：**63 tests / 0.165s / OK**。测试中预期的 OCR 配置失败打印不影响测试结论。


以上验证结果均通过。
## 边界与回退

未改真实 workflows、manifest、路由、凭据、完成态或上传；未调用模型；未 commit。提取函数保留其它代理原有 PDF OCR 修改。回退仅移除本次 fallback 小分支、`_weak_numbers` 的说话人图片空位分支和新测试即可。
