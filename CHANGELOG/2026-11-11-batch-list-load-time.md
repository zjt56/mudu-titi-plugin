# 2026-11-11 批次列表加载超时：审核投影去重哈希、进程外快照与摘录缓存

## 背景

用户反馈（m11965）：「试卷审核页面，加载试卷列表加载不出来」。现场复现：`GET http://127.0.0.1:8791/api/batches` 52.4 秒后返回 `基础连接已经关闭: 接收时发生错误`——前端 `lib/client.js:394 jget` 的预算只有 15 秒，接口必然失败。

根因不是网络也不是数据损坏，而是**冷投影 13 个批次的成本太高**（逐批串行 68 秒）：

1. `review.acceptable_unit_bindings` 每次调用都用 `_plan_input_key` 现算全部轮次输入摘要。`review.evaluate_rounds → rebase_rounds → reusable_checks → _payload_reusable_checks → _unit_binding_matches` 一条单批投影会触发 **480 次**，累计 31552 次 `Path.resolve`、16078 次 `_file_digest`；`cProfile` 里 `review.evaluate_rounds` 占单批 7.737 秒中的 7.18 秒。
2. `_confirmed_review_units` 每批被深拷贝几十次；`review_rounds._source_excerpts` 每单元扫全文，单批 1677 次。
3. 进程重启后没有任何可用缓存，首屏必须全部重算；模型/批次并没有坏，只是没人等得起。

## 改了什么

- `app/workflow/review.py`
  - `acceptable_unit_bindings` 的缓存键从「现算全部输入摘要 + 计划摘要」改为 `(str(batch), _digest(plan))`：`plan` 本身已携带 `input_hashes`，输入变化必然换计划摘要，命中语义不变而 480 次重算归零。
  - 新增 `_units_revision(batch)` / `_units_cached(batch)`（按 `01_source/structure.confirmed.json` 的 dev/ino/size/mtime_ns/ctime_ns 记忆解析结果），`_confirmed_review_units` 只做 `deepcopy`。
  - `_unit_binding_matches(batch, old_plan, plan, uid, accepted=None)` 允许传入已算好的接受集合；`_payload_reusable_checks` 每条记录只在首次遇到摘要不符时算一次接受集合。
  - **`_plan_input_key` 保持纯内容哈希**（中途试过按 stat 记忆，被 `test_content_labeling_rule_change_invalidates_real_review_units` 判死：规则文档内容变了但 stat 不变时必须换键），已回退，`git diff` 确认其函数体与 HEAD 一致。
- `app/workflow/review_rounds.py`：新增 `_EXCERPT_CACHE` 与 `_excerpt_cache_key(source, selected, force, adjacent_units)`（键 = `sha256(source)` + 每个单元的 `unit_id/unit_kind/stem/repr(source_span)`；不用 `id(source)`，源码每次读文件都是新对象，用 id 永不命中），`_source_excerpts` 变缓存包装，原实现改名 `_source_excerpts_uncached`。缓存值同时持有 source 引用，防止 id 复用。
- `app/workbench/server.py`
  - 新增进程外投影快照：`_overview_snapshot_path()`（`WF_CACHE_DIR` 或 `%LOCALAPPDATA%/md-upload-tiku/overview-snapshot.json`）、`_load_overview_snapshot()`、`_save_overview_snapshot()`（`version:1`，每批 `{folder,key,value}`，原子 `os.replace`，写后同步内存副本）。
  - `overview()` 在内存未命中时，若快照里该批次的 `key` 与当前 `_overview_key(folder)` **完全一致**才复用，否则照常重算；`list_batches()` 结束时保存快照。
  - `warm_batch_list()` 在 `main()` 里以 daemon 线程预热一次列表，首屏直接命中。
- 新回归测试：`app/workflow/test_review_binding_cache.py`（4 例：同内容同键、内容变化换键、绑定检查不得再调 `_plan_input_key`、单元解析每修订只一次）、`app/workbench/test_overview_snapshot.py`（3 例：快照同指纹命中不重算、指纹不符必须重算、损坏 JSON 忽略）。

## 没改什么 / 边界

- 审核语义没变：语义投影、可接受旧摘要集合、`review_gate=needs_review`、13 条待人工差异都在；没有为了让列表变快而放宽任何校验，也没有 resolve 任何差异。
- 快照只存**本机本 WORKFLOWS** 下的投影，且必须指纹一致才用；指纹由原有的 14 个关键文件 mtime/size 决定（与原内存缓存同口径），不新引入陈旧数据类别。
- 不并行投影：`ThreadPoolExecutor(max_workers=6)` 实测冷启 16.26s，比串行还慢（`_lock` 串行化 + GIL），已回退为串行实现。
- 未改用户数据、未改 `manifest.json`、未上传；`config/model-router.json`（设备本地）与未知 `.dsh-office-*` 目录不提交。

## 验证

- 单批投影 `7.737s → 1.453s`；13 批串行冷启 `68s → 8.88s`；命中快照 `6.48s → 3.40s`（摘录缓存后）。
- 真实后端：`GET /api/batches` 返回 200、1.03–1.46s，`overview-snapshot.json` 1.92MB 已落盘（18:41:11）。
- `python -m unittest workflow.test_review_rounds workflow.test_review_local_reuse workflow.test_review_child_import workflow.test_ingest_binding_equivalence workflow.test_review_binding_cache` → **115 tests OK（67.2s）**；`workbench.test_overview_snapshot` → **3 tests OK**。
- 前端不再触发 `请求超过15秒` 的兜底提示，审核页能列出 13 个批次且课时批次显示 13 条差异。

## 回退方式

`git revert <本次提交>` 即可：改动集中在三个模块的缓存/快照层，没有数据结构、审核契约或产物格式变化；删除 `%LOCALAPPDATA%\md-upload-tiku\overview-snapshot.json` 可单独让快照失效而不影响代码。
