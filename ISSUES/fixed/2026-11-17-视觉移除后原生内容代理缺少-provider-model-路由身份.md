# 卡点：视觉移除后原生内容代理缺少 provider/model 路由身份

- id: 2026-11-17-视觉移除后原生内容代理缺少-provider-model-路由身份
- status: fixed
- batch: 【木牍教育】19年安徽中考英语-2
- batch_dir: workflows/【木牍教育】19年安徽中考英语-2
- stage: knowledge
- check: NATIVE_CONTENT_ROUTE_IDENTITY_MISSING
- created: 2026-11-17T17:25:26+08:00
- last_seen: 2026-11-17T17:25:26+08:00
- occurrences: 1
- fixed_at: 2026-11-17T17:25:59+08:00
- fix_commit: 88d17d6
- verification: test_content_agent+test_content_integration 91项通过（1跳过，单独运行1项通过）；批次19-2 knowledge completed
- title: 视觉移除后原生内容代理缺少 provider/model 路由身份

## 现象（卡点）

knowledge：Native agent requires selected provider/model route identity

## 具体检查与判定

- 失败检查：NATIVE_CONTENT_ROUTE_IDENTITY_MISSING
- 判定口径：32aa4e3 删除视觉链路时移除了 local_agent 原生分支的 _content_route_identity 注入；content_agent 严格拒绝缺失路由身份的任务
- 修复方向：local_agent 新增 _selected_content_route 从 Harness agent-default-model 读取 provider/model 并注入；knowledge 重跑完成，不写死模型

## 修复记录

- 修复方式：local_agent 原生内容分支新增 _selected_content_route，从 Harness agent-default-model 注入 provider/model
- 修复提交：88d17d6
- 复验证据：test_content_agent+test_content_integration 91项通过（1跳过，单独运行1项通过）；批次19-2 knowledge completed
- 标记时间：2026-11-17T17:25:59+08:00
