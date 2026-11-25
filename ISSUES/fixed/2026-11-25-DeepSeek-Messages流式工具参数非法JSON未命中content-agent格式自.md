# 卡点：DeepSeek Messages流式工具参数非法JSON未命中content-agent格式自愈

- id: 2026-11-25-DeepSeek-Messages流式工具参数非法JSON未命中content-agent格式自
- status: fixed
- batch: 3626秋八上人教版课时A计划Unit5WhataDeliciousMeal!第
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/3626秋八上人教版课时A计划Unit5WhataDeliciousMeal!第
- stage: knowledge
- check: content_agent.toolArgumentError
- created: 2026-11-25T17:51:30+08:00
- last_seen: 2026-11-25T17:51:30+08:00
- occurrences: 1
- fixed_at: 2026-11-25T17:53:59+08:00
- fix_commit: -
- verification: content-agent.test.cjs 56/56 通过；批次 3626秋八上人教版课时A计划Unit5WhataDeliciousMeal!第 的 knowledge 阶段复验通过
- title: DeepSeek Messages流式工具参数非法JSON未命中content-agent格式自愈

## 现象（卡点）

knowledge阶段在sec-83722947e5c25c11-u60与sec-4b13e9eaca91963f-u102报 DeepSeek Messages stream: tool input is invalid JSON 直接失败

## 具体检查与判定

- 失败检查：content_agent.toolArgumentError
- 判定口径：lib/content-agent.js 的 toolArgumentError 仅匹配 Tool call "<name>": ... 格式，未识别 DeepSeek Messages 流式解析器在命名前抛出的 tool input is invalid JSON，跳过同会话格式自愈重试直接 cancel 为 failed
- 修复方向：扩展 toolArgumentError 识别流式工具参数非法JSON错误，触发同会话有界格式自愈(FORMAT_RECOVERIES=2)并补单测，随后复验该批次knowledge阶段

## 修复记录

- 修复方式：扩展 lib/content-agent.js 的 toolArgumentError 识别 DeepSeek Messages 流式层抛出的 tool input is invalid JSON，触发同会话格式自愈重试(FORMAT_RECOVERIES=2)，并补齐回归测试
- 修复提交：-
- 复验证据：content-agent.test.cjs 56/56 通过；批次 3626秋八上人教版课时A计划Unit5WhataDeliciousMeal!第 的 knowledge 阶段复验通过
- 标记时间：2026-11-25T17:53:59+08:00
