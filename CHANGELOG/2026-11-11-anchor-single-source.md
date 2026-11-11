# 2026-11-11 空位号/题号识别收敛为单一实现，并强化结构与题型提示词约束

## 背景

用户要求“修复先进代码层面，然后加强约束，加强提示词约束”。此前空位号识别分散：`structure_gate._scope_anchors`
自带一套正则、`preview._weak_numbers` 另有一套，易漂移、易复发（批次02/03同类误判即为两处口径不一致所致）。

## 改了什么

- `app/workflow/preview.py`：把空位号两种合法写法收敛为常量 `_BLANK_BEFORE_NUMBER`（空位在号前）
  与 `_NUMBER_BEFORE_BLANK`（题号在空位前，且其后必须确有 `　`/`__`/`_`），`_weak_numbers` 统一使用，
  成为全项目唯一空位号识别源。
- `app/workflow/structure_gate.py::_scope_anchors`：删除自带正则，改为对 `normalize_blank_text` 规范化后的行
  调用同一 `_weak_numbers`；`_strong_number` 也基于规范化行，避免 `</u>.` 句点误判。
- `app/workflow/sync_practice.py::prompt_block`：新增约束——完形/短文填空材料行内 `<u>　n　</u>`、`2.　　`、
  `Tony.1.____` 是空位号（子题号本体），不是印刷题号、不与 `( )n.` 选项行重复，也不因 `</u>.` 后句点误识别。
- 回归：`test_sync_practice::test_prompt_block_is_compact_and_scoped` 新增断言 prompt_block 含“空位号/不是印刷题号”；
  `test_structure_gate` 既有两条编号空格用例继续覆盖代码层。

## 没改什么 / 边界

- 不放宽题号唯一绑定：真实同窗重复、跨材料、缺失题号仍阻断。
- `question_identity.numbered_projection` 仍按原逻辑切分（其结果与共享 `_weak_numbers` 一致，已由身份全套测试覆盖）。
- 不改审核/上传口径，不改用户数据。

## 验证

- 结构/身份/同步/题型/预览/角色相关共 241 项通过（4 skipped）。
- 真实批次 02（空白网格/图片，9 单元）与 03（完形/补全对话，25 单元）结构闸门均 passed；02 无误伤。

## 回退

回退本提交恢复各处自写正则；不会改动用户批次数据，但空位号口径将重新可能分叉漂移。
