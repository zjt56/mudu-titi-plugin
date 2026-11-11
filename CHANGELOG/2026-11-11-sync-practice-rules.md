# 同步练习 / 课时同步判定口径落到插件代码

- 日期：2026-11-11
- 触发：用户 m01225「这个规则添加到该项目的插件代码，并注重是同步练习类和课时同步的规则」

## 一、背景

盘点 26秋《课时A计划》英语 RJ 七上（305 处）、八上（283 处）、九上（266 处）正文共
**854 处大题**，并由用户逐条看图确认组题/单独题，最终定为 78 行、14 个主类、
组题 371 / 单独题 483。这些结论此前只存在于桌面 `英语题型汇总.xlsx` 和 `.tmp` 脚本里，
插件本身没有任何约束——换一批同系列材料，题型与组单仍会各判各的。

## 二、改了什么

| 文件 | 变化 |
|---|---|
| `app/references/sync-practice-rules.json`（新增） | 唯一口径来源：`applies_to`（适用范围）、`granularity`（组/单定义）、`type_kind`（26 个主类默认组单）、`rules`（32 条有序关键词规则，含 `none` 排除与 `subtype_any` 附加条件）、`containers`（综合阅读提升/单元话题写作/☆拓展设问）、`notes` |
| `app/workflow/sync_practice.py`（新增） | `normalize` / `classify` / `classify_with_body` / `refine` / `kind_for` / `looks_like_sync_practice` / `prompt_block` / `audit_units` / `validate`，命令行自检 `python -m workflow.sync_practice` |
| `app/workflow/structure_ai.py` | `_classify_prompt` 在识别到同步练习特征时追加 `prompt_block()`；`auto_classify` 归类后用 `audit_units` 与原卷比对，差异写进 `01_source/structure.ai.classified.json` 的 `sync_audit` 并随返回值带出 |
| `app/workflow/test_sync_practice.py`（新增） | 17 条回归：规则合法性、代表标题映射、组/单判据、`refine` 空数/小题数/拓展设问、材料范围识别、CLI 自检 |
| `app/docs/sync-practice.md`（新增） | 口径说明、适用范围、组/单结论、容器、代码用法、扩展方式、已知差异 |
| `AGENTS.md` | 新增硬规则 25 |
| `CHANGELOG/RULES.md` | 追加同日规则台账条目 |

关键口径（详见 `app/docs/sync-practice.md`）：

- group：完形填空、阅读理解、任务型阅读、选词填空、短文填空、教材语篇填空、补全对话、
  语音题整大题（含辨音选择/音标选词/字母表补全/大小写判断）、看图题（下图/图片信息/图示/家谱/
  地图）、四线三格、填词游戏、音标方框选词、（can/can't/could/couldn't）、（特殊疑问词完成对话）；
- standalone：单项填空、完成句子、单词拼写一句一空、翻译、改错、书面表达；
- `☆拓展设问` 并入所属组题，不计入空数（八上完形曾因此被算成 11 空）；
- `综合阅读提升`、`单元话题写作` 只是版块容器，不单独计数。

## 三、没改什么 / 边界

- **不套用到非同步练习材料**：单元卷、期中期末卷、月考卷、中考真题/模拟卷不注入该口径，
  `prompt_block` 只在 `looks_like_sync_practice` 为真时进入提示词。
- **不改闸门**：`sync_audit` 只留痕，结构闸门仍以原卷证据（`source_span`、题号、原文逐字匹配）为准；
  不因口径差异自动改 `type`/`unit_kind`，也不放行。
- **不改历史批次**：七上旧批次把「写出空白处所缺字母」「判断大小写是否一致」「四线三格」按单独题
  录入（`RJ7A-U1-01-401`、`-403…410`、`RJ7A-U2-01-001…003`），与现口径不同，已写入
  `app/docs/sync-practice.md` 第八节留档，不回头改写；新批次按现口径。
- **不改 `agent_tasks` 的 refs**：避免输入哈希变化让在途批次的任务集体过期。
- 书面表达子类（列中文要点/思维导图/表格/图文结合…）规则里保持 `subtype: null`，必须看正文
  提示形式才能定，不按标题猜。

## 四、如何验证

```
PYTHONPATH=app python3 -m workflow.sync_practice
# 规则自检: 通过 / 主类数: 26 规则数: 32

PYTHONPATH=app python3 -m unittest workflow.test_sync_practice
# Ran 17 tests ... OK
```

实测对照（用三册 854 处真实标题 + 原卷正文切片回归）：

- 标题级映射：**854 / 854 全部命中，0 未识别**，`validate()` 无问题；
- 标题 + 正文切片（`classify_with_body`）：854 处中 **807 处与人工确认结果完全一致**；
- 剩余 47 处差异全部有明确原因，且不是规则错误：
  - 46 处是书面表达——子类由正文提示形式决定（列中文要点/思维导图/表格/图文结合），
    规则保持 `null` 待模型看正文；
  - 1 处是「根据短文内容完成思维导图」——规则按目录语义记 `任务型阅读/信息摘录`，
    早先脚本记的是 `阅读综合`，属清单侧待统一的口径点。

`workflow.test_structure_ai` 的 `test_classify_uses_real_local_agent_protocol` 在本机失败，
原因是假 Agent 把中文 JSON 打到 Windows 控制台（默认 cp936）后由 `local_agent` 按 utf-8 读取，
与本次改动无关（改动前 `git stash` 复测同样失败），Linux/CI 不受影响。

## 五、回退方式

删除 `app/references/sync-practice-rules.json`、`app/workflow/sync_practice.py`、
`app/workflow/test_sync_practice.py`、`app/docs/sync-practice.md`，并撤销
`structure_ai.py` 中的 import、`sync_hint` 注入与 `sync_audit` 两处改动即可；
`AGENTS.md` 规则 25 与 `CHANGELOG/RULES.md` 对应条目一并删除。
