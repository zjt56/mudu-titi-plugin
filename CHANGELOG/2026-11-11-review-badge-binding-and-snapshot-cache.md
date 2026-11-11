# 待审核标签按来源身份绑定 + 审核投影缓存

日期：2026-11-11
触发：用户反馈（m01175）

> 目前存在两个问题，一，课时刚录的有差异却没有待审核标签，第二就是题目加载缓慢，切换题目的时候，试卷审核列表加载试卷也有点慢

## 一、背景：两个症状同一个批次的现场

批次 `01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio`（scoped-v1，`Ⅰ/Ⅱ/Ⅲ` 每个大题都从 1 重新编号）：

- **标签漏标**：`07_review/manual-review-queue.json` 里有 13 条未裁决差异（11 个不同 unit），
  列表接口却只标出 1 行「待审核」，其余 10 行看起来"干净"。
- **慢**：`GET /api/batches/<name>/units` 单次 0.85–0.94 秒，切换每道题都要等；
  `GET /api/batches`（13 个批次）冷 3.87 秒。

## 二、根因

1. `app/workbench/server.py::findings_for` 只按**题号数字**绑定差异：

   ```python
   number = as_number(number_map.get(unit) or unit)
   if number in wanted:
   ```

   scoped-v1 的单元身份是 `sec-12c7e16eaf5bfe9c-u36` 这种，`as_number()` 退化成取最后一段数字；
   `Ⅰ` 的第 1 题和 `Ⅱ` 的第 1 题都是 `1`，于是别的大题的差异被算到本行、
   本行真实差异反而漏掉（`sec-12c7e16eaf5bfe9c-u36` 和 `sec-a62dcefde4641bd9-u73` 各有 2 条差异，行上只显示 1）。
2. 详情接口每调用一次都从零重建审核投影：`review_snapshot` → `review.evaluate_rounds`
   占单次耗时 1.74/1.92 秒，其中 `_reuse_source` 被调用 104 次、`_verify_round_sources` 106 次，
   即**每切一道题都把全批 34 条检查的逐字绑定重验一遍**。

## 三、改了什么

`app/workbench/server.py`

- `findings_for(queue, numbers, number_map, unit_id="", scoped=False)`：scoped 批次只认
  `item["unit_id"] == 行 unit_id`；批次级差异（无 `unit_id`）不再钉到具体题行；非 scoped 旧批次保持原数字绑定。
- `_overview_uncached`：先算 `scoped_rows = any(row.identity_version == "scoped-v1")`，
  再把 `unit_id` / `scoped` 传进 `mark_row`。
- 新增 `_REVIEW_SNAPSHOT_CACHE` + `_review_inputs_key(folder)`：指纹覆盖
  `07_review/{review-rounds,manual-review-queue,review-gate,report,content-review,metadata-review}.json`、
  `workflow/manifest.json`、`01_source/structure.confirmed.json`、`06_batch/{batch,batch_meta}.json`、
  `01_source/*.txt`、`candidates/*.json` 的 `(mtime_ns, size)`；`review_snapshot()` 命中与写入都 `deepcopy`，
  原实现保留为 `_review_snapshot_uncached()`。
- `_overview_key` 追加 `("server-code", server.py 的 mtime_ns, size)`：否则进程外落盘投影会在代码升级后
  继续按旧逻辑复用（实测踩到过，必须删 `%LOCALAPPDATA%\md-upload-tiku\overview-snapshot.json` 才生效）。

新增回归：`app/workbench/test_findings_scoped_binding.py`（9 项）
—— 同号不同单元互不串标、同单元多条差异全保留、批次级差异不钉行、旧批次仍按题号、
scoped 行不受数字列表影响；缓存命中不重复计算、返回深拷贝（调用方改返回值不污染缓存）、
输入文件变化即失效、输入缺失也能缓存。

## 四、没改什么 / 边界

- 不改审核结论、不自动 resolve 差异：`review_gate=needs_review` 仍是设计状态（硬规则 6）。
- 不改前端显示口径：标签比对与「不一致」标红仍按短名数组比较（`lib/client.js::kpShort/cmpRow`）。
- 缓存只是性能优化：输入指纹变化必然重算，未引入跨批次共享或跨进程缓存。
- 诚实边界：scoped 批次里没有 `unit_id` 的批次级差异不会出现在单行徽标上，只在整批差异数里体现。

## 五、验证（实测数字）

- 该批 `overview`：`needs_human` **1 → 11**，`counts` 由 `{'ok': 24, 'attention': 3}` 变为
  `{'ok': 16, 'attention': 11}`；`sec-12c7e16eaf5bfe9c-u36`、`sec-a62dcefde4641bd9-u73` 行上 `open_count=2` 正确。
- 详情：**0.85–0.94s → 0.062–0.088s**（首次 0.78s 建缓存）。
- 列表：`list_batches` **3.87s → 0.35s**（热）；进程外 `/api/batches` 实测 **1007ms 冷 → 140ms 热**。
- 测试：`python -m unittest workbench.test_findings_scoped_binding` → **Ran 9 tests, OK**；
  `workbench` 全部 14 个 test 模块 **Ran 81 tests**，其中 6 项失败均为既有问题
  （`media_transfer._open_root` 依赖 `os.O_DIRECTORY`、`efficiency_projection` 环境差异、
  scoped 预览切片），已在干净 HEAD `569029f` 的 worktree 复跑，逐项相同。
- 线上后端（pid 36348，18:47:56 启动）晚于 `server.py` 最后修改（18:40:42），即运行的是修复后代码。

## 六、回退

`git revert <本提交>` 即可；缓存上限 64 项、按指纹失效，回退不需要清任何持久文件
（若残留 `overview-snapshot.json`，其键含代码指纹，不会命中旧逻辑）。
