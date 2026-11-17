# 2026-11-17：单元覆盖不全的报错点名缺失/多余单元

## 背景/触发

批次 `【木牍教育】16年安徽中考英语` 的前置批次组装（含题型）失败：

```
Artifact must have unique exact confirmed unit coverage
```

该批次有 48 个确认单元，原生内容代理一次只提交了 17 行候选（模型输出长度受限），
然后连续三次提交同一规模的候选，校验器每次都只回一句通用错误，模型无从知道缺了哪些
单元，最终被判 `validation_no_progress` 失败。

## 改了什么

- `app/workflow/agent_tasks.py::_coverage`：覆盖不全时错误消息点名
  「缺少单元 N 个：…」「多出单元 N 个：…」「重复单元 N 个：…」以及
  候选行数与确认单元数，符合硬规则 19「校验报错必须点名缺/多的键与不匹配的题号」。
- `app/workflow/test_agent_tasks.py`：新增 `test_coverage_error_names_missing_and_extra_units`。

## 没改什么 / 边界

- 仍要求逐单元恰好一行、集合完全一致，不放宽覆盖校验、不自动补单元。
- 不改候选内容、不改上传边界；原生分片传输（`content_stage_candidate`）由并行的
  另一处改动负责，本次只改错误可读性。

## 如何验证

- `workflow.test_agent_tasks`：65 项通过。
- 真实批次 `【木牍教育】16年安徽中考英语`：改用 CLI 路径重跑 `preassemble` 成功
  （48 单元全部判定，无漏项），`types` 交接通过。

## 回退方式

删除 `_coverage` 中缺失/多余/重复明细拼装，恢复单句错误消息即可。
