# 停用视觉理解（删除整页/栏目/图片语义/视觉传输）

## 背景

用户 2026-11-17 提出「视觉理解先不急，可以先取消」，并在追问后确认：**彻底删除视觉相关代码**，缺视觉时**按无视觉继续、不阻断**，且 **visual_transport 也一起删**（它会真的调用图片模型）。

此前结构确认依赖整页渲染 + 视觉栏目归属 + 图片语义，用来判断无号栏目题的归属。用户认为这条链路不稳定、且不是当前优先事项。

## 改了什么

删除模块：
- `app/workflow/visual_columns.py`（整页栏目归属）
- `app/workflow/source_pages.py`（整页渲染/页图证据）
- `app/workflow/visual_review.py`（图片语义）
- `app/workflow/visual_crosscheck.py`（Word×视觉交叉印证）
- `app/workflow/visual_transport.py`（图片模型传输）
- 对应测试：`test_visual_columns.py`、`test_source_pages.py`、`test_visual_review.py`、`test_visual_crosscheck.py`、`test_visual_transport.py`、`test_structure_visual_binding.py`、`test_answer_lesson_bands.py`

重写为纯文本：`app/workflow/lesson_bands.py` 改为 `lesson-text-bands-v1`，只用印刷文本行识别 `A 基础巩固 / B 能力提升` 板块与栏目标题，不读图片、不渲染、不调模型；保留 `canonical_label/for_text/load_verified/annotate_sections/build` 同名接口，调用方无需迁移。

调用方改造（保持签名兼容、传入空列，缺视觉不阻断）：
- `structure_gate.py`：`columns=[]`；`column_ownership` 恒为 `not_required`；删除整页证据读取与视觉证据报告字段。
- `structure_ai.py`：不再注入 `visual_columns`；无号单元只用「唯一重建的无号题」或「完整合法来源节」证明；原生结构取证仍继续（文字任务不受影响）。
- `question_content.py` / `preview.py` / `question_draft.py` / `content_structure.py`：去掉视觉列依赖，快照不再绑定整页页图/栏目产物。
- `content_media.py`：保留参数/快照/哈希校验，图片检查统一返回 `MEDIA_VISION_DISABLED`（`model_calls=0`），不再调用图片模型。
- `content_agent.py` / `local_agent.py`：路由漂移检测改为只读任务自身绑定的 `expected_route`，不再解析图片桥。
- `extract.py` / `runner.py`：删除视觉构建钩子与视觉可用性判定。

## 没改什么 / 边界

- 原文保真、OCR、结构/题号/材料/来源校验、审核差异、上传边界一律不变。
- 无印刷号题仍要求来源证明（唯一重建/完整合法来源节），不会因取消视觉而放宽成「猜挂最近题」。
- 图片仍是附件，按字节与指纹绑定证据，只是不再做图内语义/栏目判定。
- 不做无视觉的「整页覆盖」宣称；缺视觉证据不阻断，但也从不把缺失伪装成通过。
- 历史批次里已有的视觉证据文件不删除、不重写，只读留档。

## 验证

- 受影响模块：`test_structure_gate`(19)、`test_structure_ai`(25)、`test_question_content`、`test_question_identity`、`test_content_media`(14)、`test_content_structure`(26)、`test_content_agent`(43)、`test_content_integration`、`test_model_admission` 全部通过。
- 分块全量（排除两个既有慢模块 `test_structure_number_inference`/`test_source_structure`）与改动前基线 `dd840be` 逐项对比：**失败 70 / 错误 26 / 跳过 20 完全一致**，仅一个偶发用例差异（`test_parallel_wave_does_not_deadlock_concurrent_mark` 单独复跑 3 次全 OK）。测试总数由 3388 降为 3257（删除的视觉用例）。
- 全仓 `py_compile` 通过；无对已删五模块的有效 import。
- `workflow.test_changelog_policy` 通过。

## 回退

`git revert` 本次提交可恢复视觉链路；`lesson_bands.py` 会回到看图版本。

## 未决点

- 本机 `visual_transport` 删除后，内容子智能体的 `inspect_media` 图片取证变为固定拒绝；如将来恢复图片取证需重新引入传输层。
- 依赖整页视觉的无号栏目题，在纯文本无法证明边界时会落入常规结构闸门诊断，而不是自动补证。
