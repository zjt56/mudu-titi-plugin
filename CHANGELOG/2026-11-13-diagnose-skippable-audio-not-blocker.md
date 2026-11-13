# 诊断不把“无听力课时”误报为缺少 audio 输入的卡点

## 背景/触发

批次 `47七上人教版课时A计划Unit5FunClubs综合阅读提升` 是课时拆分件，试卷本身**没有听力**。
诊断却上报：

```
卡点诊断 → 听力切段与音频填充（audio）：缺少必需输入
原因：缺少输入：audio
```

`audio` 阶段 `requires=("audio",)`、`optional=True`，`mf.skip_reason()` 已能按
“确认结构全部为非听力题”判定该阶段应跳过；但 `diagnose()` 没有消费 `skip_reason`，
把 `status=pending` + 缺 audio 输入当成“阻塞卡点”，与用户“课时试卷允许没有听力”的规则冲突。

## 改了什么

- `app/workflow/diagnose.py`：诊断循环内新增 `_input_skip_reason(stage)`，
  仅当阶段 `status=pending` 且 `mf.skip_reason()` 非空时，标为“可跳过（…）”，
  `blocking=False`、`failure_class` 置空、`next_action` 给出跳过说明、不进入
  `blocked_at`。已 `FAILED/needs_review/blocked` 的阶段不受跳过理由掩盖。
  阶段仍保留在 `items` 里供展示，避免下游消费者诊断断链。
- `app/workflow/test_diagnose.py`：新增
  `test_skippable_audio_noise_batch_is_not_a_blocker`（无听力结构 → audio 非阻塞、reason 含“可跳过”、blocked_at 不为 audio）。

## 没改什么 / 边界

只改诊断展示与卡点判定，不改阶段契约、不自动放行任何阶段、不改上传边界；
`audio` 仍需真实音频才会实际执行切段。真实执行失败（FAILED/needs_review）照旧上报。

## 如何验证

- `workflow.test_diagnose` + `workflow.test_diagnose_memo` → **55 passed**。
- `workflow.test_runner_auto` + `workflow.test_optional_inputs` + `workflow.test_stage_semantics` → **101 passed**。
- 真实批次 `47…Unit5FunClubs综合阅读提升`：status 不再出现 audio 卡点；批次已推进到
  `review_gate=needs_review`（2 项质量差异待人工，设计态，未上传）。

## 回退方式

删除 `diagnose()` 中 `input_skip_reason` 相关分支，恢复原“缺失必需输入→blocking”逻辑。
