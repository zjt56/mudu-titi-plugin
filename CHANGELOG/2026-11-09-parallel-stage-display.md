# 2026-11-09 流程进度显示：并行阶段同时“处理中”，完成态不再被压回

## 背景 / 触发

用户反馈：批次组装完成后进入听力阶段，流程显示“自动跳过听力到答案解析，解析完成又跳回听力，
答案解析完成状态又消失，然后又突然到来源难度”，与既定的并行处理逻辑不符。

排查结论（只读）：执行层按 `contracts.depends_on` 正确并行（`auto._advance_locked` 的
`audio_allowed` 先切听力 + `PARALLEL_AGENTS_ENABLED` 一次性并发 types/answers/audio，
再并发 explanations/knowledge）；问题在**显示层**：`compactFlowStages` 把 18 个阶段压成
9 个节点连成一条链，且 `stage_cards` 只暴露单个 `current`，并行完成顺序交错时画面
表现为“来回跳”，已完成的组被下一个成为 current 的组短暂盖掉。

## 补充（2026-11-09 下午）：题型判定并入「批次组装」节点

用户指出“题型大部分在题干就能表现出来，放在组装批次比较合理”。核查后确认：
`types` 的依赖只有 `structure_gate + preassemble`，即**批次组装一完成，题型判定
就在同一批里立即开始**，与答案并行；执行顺序本就等价于“组装后马上判题型”。
因此采用显示层合并（不改执行契约）：

- `lib/client.js::compactFlowStages`：把 `preassemble` 与 `types` 合并为一个节点
  「批次组装与题型」，节点完成态要求两者都完成；题型不再单独占一个节点。
- 知识点节点改为只含 `knowledge`，标题「知识点与教材」。

未采用“把 types 阶段真正并入 preassemble”的执行层改法：`types` 是导出任务的
绑定身份（`workflow/agent-bindings/<task_id>.json` 的 stage、角色提示词
`app/prompts/roles/types.md`、多个调度测试与 `agent_protocol._spec` 都按阶段 id
校验），直接删除该 id 会破坏历史任务绑定与 10+ 个调度测试，需要独立的版本化迁移，
不适合与显示修复混在一起。

## 改了什么（只改显示）

1. `app/workbench/server.py`
   - 新增 `active_stage_ids(manifest, cards)` 与 `manifest_dependency_satisfied(...)`：
     纯只读投影，列出“未完成且所有声明依赖已满足”的阶段集合（依赖满足含已执行、
     skipped、以及 review_gate 的“已执行待人工”），不做 consumer 校验、不改台账。
   - `stage_cards` 为每张卡加 `active` 布尔，并保持原 `status`/`current` 语义不变。
2. `lib/client.js`
   - `compactFlowStages`：组内存在 `active` 成员且未完成时，标记 `inProgress=true`
     （组状态仍算 pending，不影响“已完成/总数”）。
   - 流程图 `isCur` 改为：`running` 或 `inProgress` 或该组含 `current` 的组都高亮，
     即并行时多个节点同时显示“处理中”，不再轮流抢唯一高亮。
   - 已 `completed` 的组状态与对勾保持不变，不因其它组 still running 而回退。
3. 新增 `app/workbench/test_stage_parallel_display.py`（8 用例）：并行阶段同时 active、
   完成阶段不 active、依赖未满足不 active、rebuild 阻止 active、skipped/完成不 active、
   review_gate 待人工解锁下游、card 保留原状态、全新批次只有 extract active。

## 没改什么 / 边界

- 不改调度顺序、依赖判定、消费者校验（`manifest.dependency_satisfied` 仍是
  dispatch 的唯一依据），不改任何阶段状态或产物。
- 不改变“已完成/总数”和排队区进度口径；“并行处理中”的组仍算未完成。
- 不改变审核、上传、发布边界；纯 UI/只读投影。
- `active` 只表示“依赖已满足、尚未完成”，不代表一定正在调用模型（模型可能排队/冷却）。

## 如何验证

- `PYTHONPATH=app python3 -m unittest workbench.test_stage_parallel_display`
  → **8 tests OK**。
- `PYTHONPATH=app python3 -m unittest workbench.test_stage_parallel_display
  workbench.test_list_projection workbench.test_upload_status` → **18 tests OK**。
- `node --check lib/client.js` → 通过。
- 用真实并行中间态 manifest 模拟：`types/answers` 完成、`explanations/knowledge` 同时
  满足依赖 → 两个都 `active`；`assemble` 因缺 explanations 不 active。
- 真实已完成批次（2016 卷）不再出现“并行阶段闪回”，`active` 只剩 review_gate/publish_gate。

## 回退

回退 `app/workbench/server.py` 的 `active_stage_ids`/`stage_cards` 改动与
`lib/client.js` 的 `compactFlowStages`/`isCur` 改动即可（删测试文件）。
