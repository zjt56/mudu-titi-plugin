# 上传 PDF 必须完成整卷 OCR

日期：2026-11-13

## 背景

用户强调：「当上传PDF时，要求必须OCR识别」。原流程虽提交强制 OCR，但 Word 主源仍可能在配套 PDF 识别失败时标记提取通过；本地适配器也可能接受只返回原生文字层的结果，已完成旧批次的重复上传未校验 OCR 证据。

## 改了什么

- `app/workflow/ocr_backend.py`：`mandatory_pdf=True` 自身强制设置 `force_ocr=True`，拒绝强制识别返回 `method=text`。
- `app/workflow/pdf_ocr.py`：整卷空文字、原生文字层冒充识别结果均记失败，不写成功的 acquisition 路径。
- `app/workflow/extract.py`：逐份检查 PDF 当前原件摘要绑定的 OCR acquisition；任一缺失/失败/空文字/原生文字层结果均使提取不通过，包括 Word 配套 PDF。保存 Word 提取结果和具体 PDF 失败原因。
- `app/workbench/server.py`：重复上传时，已完成提取的候选批次须具备每份 PDF 的有效 OCR，否则跳过旧批次，为本次上传创建新批次。处理中/待处理去重语义保留。
- 同步 `contracts.py` 的阶段说明、`app/docs/extraction.md`、根规则 28 和 `CHANGELOG/RULES.md`，补齐适配器、提取与上传去重回归。

## 边界

Word 同角色内容仍优先；不修改 OCR 服务阅读顺序、不增加重试、不改远端上传授权；保留 JSON/Markdown 双格式。未改写、重跑或删除任何真实批次，也未安装或重启运行中插件。缺少有效 OCR 的旧完成批次不会被改造成完成态。

## 验证

- Python 3.12.10 / Node.js v24.14.0。
- `PYTHONPATH=app python -X utf8 -m unittest workflow.test_pdf_ocr workflow.test_pdf_ocr_integration workflow.test_ocr_backend workflow.test_material_upload_roles workflow.test_source_policy workflow.test_workbench_decisions workbench.test_upload_status`：98 项，97 通过、1 跳过。
- 扩大 OCR/提取/消费链路组合（上述 7 个模块，加 `workflow.test_ocr_assembly_images`、`workflow.test_ocr_assembly_source`、`workflow.test_ocr_structure_gate`、`workflow.test_extract`、`workflow.test_docx_extract`）：157 项，156 通过、1 跳过，用时 32.227 秒。
- `node --test backend/*.test.cjs`：374 项，355 通过、18 失败、1 跳过；在独立临时目录运行修改前 `206c68f` 基线得到完全相同的数字和 18 个失败名称，未新增后端失败。既有失败集中于 Windows shell/python3 调用及生命周期测试。
- `workflow.test_changelog_policy`：5 项，3 通过、2 失败；修改前基线同样失败于既有 `2026-11-12-harness-content-agents.md` 缺少“改了什么”及索引缺少 `2026-11-12-review-newest-first.md`，本次记录已满足格式和索引要求。
- 全量工作流 discover 已尝试，但超过六分钟无最终汇总后停止；不能记为全量通过。已知历史全量测试存在未隔离模型调用风险，本次未证实实际调用模型。以上 157 项相关回归完成。
- `git diff --check`：通过。

## 回退

按本次提交整体回退代码、测试与规则文档即可；不需要修改任何用户批次数据。回退会恢复此前配套 PDF OCR 失败可能仍通过的行为。
