# 2026-11-11 提取来源策略与AI运维固定消息区

## 背景

用户要求把「Word > 带文字层 PDF > 扫描件」写入插件代码，并要求AI运维提示固定在下方区域、内部上下滚动，不能因消息堆叠上推流程图。

## 改动

- `app/workflow/source_policy.py`：共享主源选择与来源报告，Word 优先、同级保序；统计 PDF 原生文字/OCR 页数。
- 上传入口已有对该共享策略的调用；补充上传集成断言。`app/workflow/extract.py` 将实际来源写入 `01_source/source-policy.json` 与提取报告，不二次重写 extraction.json。
- `lib/client.js::OperationsPanel`：消息区固定 132px、内部滚动，工具栏和状态独立保留；重复接手提示去重并最多显示5条，用户上滑时不被自动贴底打断。
- `backend/style-inject.test.cjs`：读取源码时归一化CRLF，修复Windows检出导致函数切片为空的测试问题。
- 根AGENTS规则28/29、提取说明和规则台账同步记录。

## 边界

不改历史试卷数据，不改变OCR辅助总开关；PDF/OCR不得覆盖已有Word原文，缺失答案/解析/大意仍按既有来源证据兜底。保留后续独立改动删除的常驻说明文案，不恢复该文案。审核与上传边界不变。

## 验证

- Python相关测试：`workflow.test_source_policy`、`workflow.test_workbench_decisions`、`workflow.test_extract`、`workflow.test_pdf_ocr_integration`，共72项通过（PYTHONUTF8=1）。
- 前端专项：operations-ui、style-inject、upload-flow-redesign、review-layout，共31项通过。
- `node --check lib/client.js` 与 `node --check lib/index.js` 均通过。
- 最终全量backend共247项：228通过、18失败、1跳过。失败为生命周期8项、模型路由6项、HTTP3项、发布交接1项，均已在修改前HEAD对照重现；原样式注入测试1项失败已通过CRLF兼容修复。未将全量测试声明为通过。
- 当前环境无浏览器自动化工具，尚未对宿主实际页面做截图验收；滚动容器与贴底行为由源码回归验证。

## 回退

回退本次提交中的来源报告接入和运维消息容器及对应测试；不改用户批次或本机模型配置。
