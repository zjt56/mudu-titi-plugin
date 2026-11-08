# 2026-11-07 修复：试卷审核页与切题加载慢

## 背景 / 触发
用户反馈：点进「试卷审核」要加载很久，切题也加载很久。

## 定位（本机实测，48 单元卷）
- 单次批次总览 `overview()` 冷启动 **10.65s**，`/api/batches` 列表 10.1s。
- 热点：`review.evaluate_rounds` 内 `_verify_round_sources → frozen_partition_digest → _digest`
  对同一冻结分片重复 `json.dumps` 上千次（单次 2809 次 dumps，3.7s）；`_load` 反复读同一批
  绑定/事务文件（约 6000 次读取，2.7s）；`prepare_round_plan` 的每单元绑定在每次请求重算；
  `advance` 每 tick 对约 15 个阶段各自重建完整 question draft。

## 改了什么（全部只读缓存，语义不变）
- `workflow/review.py`
  - `_load`：按（路径, mtime, size）按键缓存 JSON 解析。
  - `_digest`：按序列化内容缓存结果。
  - `frozen_partition_digest`：改为内容键缓存（`_freeze` + `_immutable_digest`），避免重复序列化。
  - `prepare_round_plan`：按输入内容哈希（含媒体观察字节）缓存计划；**进出均深拷贝**，调用方改动不会污染缓存。
  - `evaluate`：按 review 目录内容哈希缓存判定（键覆盖绑定/分区/事务/规则/输入字节）。
  - `evaluate_rounds` **不做结果缓存**（防止隐藏变化证据；仍是直接调用）。
- `workbench/server.py`：`overview` 按批次文件指纹缓存投影，`cached_list_batches` 直接受益。
- `workflow/stage_semantics.py`：曾尝试缓存 stage 输入签名，因会漏掉原始文件字节而**已回退**，保持逐次真实计算。

## 结果
- 批次总览：`10.65s → 0.87s`（首次），随后命中缓存 `~0.0001s`。
- 切题/推进决策：`~2.0s → ~0.96s`（未缓存 stage 签名，稳妥优先）。

## 没改什么 / 边界
- 不缓存任何"审核结论"到磁盘；缓存都在进程内，键覆盖全部输入内容，文件一改即失效。
- 未放宽任何质量/上传边界；`release_ready` 逻辑未动。

## 如何验证
- 全量 Python **1626 项通过（0 失败 0 错误，17 跳过）**，含 review 复用/一致性/阶段语义这些对"缓存不得掩盖变化"最敏感的用例。
- 手工计时见上（48 单元真实批次）。

## 回退方式
移除 `review.py` 的 `_load/_digest/_immutable_digest/_PLAN_CACHE/_EVALUATE_CACHE` 与
`server.overview` 指纹缓存即可；均无数据结构变更。
