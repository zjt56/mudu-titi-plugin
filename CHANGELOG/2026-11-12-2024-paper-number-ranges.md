# 2026-11-12 修复线上 2024 卷独立题缺 numberStart/numberEnd（页面显示单元序号）

## 背景 / 触发

用户核对线上 2024 安徽英语试卷时发现：听力短对话 1–5、单项填空 21–30、单词拼写 76–80
这些独立题单元没有真实题号，页面只能回退显示「第几个单元」，例如单项填空排在第
10–19 个单元，就显示成 `10.`–`19.`；题组（6–20 等）因为子题带题号显示正常。
书面表达没有印刷数字题号，应保持空。

用户原话要点：「这是为了避免把『第几个审核/上传单元』误当成『原卷题号』。正确修复
需要给 2024 试卷这些单元补上 numberStart / numberEnd。」

## 根因

1. 本地批次其实有题号：`06_batch/batch.json` 中 `q-21` 的 `number=21`、`numbers=[21]`。
2. 但上传载荷按设计把 `number` / `numbers` / `numberStart` / `numberEnd` 当作本地记账
   字段剥掉（`app/workflow/upload_payload.py` 的 `LOCAL_FIELDS`），远端上传接口本身也
   没有题号字段（`questions` 表无 number 列）。
3. 远端试卷档案 `paper_items.number_start/number_end` 是独立维护的。2024 卷当前是
   legacy 自动档案：`bootstrap_legacy_papers()` → `inferred_paper_numbers()` 只会从
   子题号（题组）或 `location` 里的「第 N 题」推断；独立题没有子题、`location` 为空，
   于是得到 NULL。
4. 页面在 `numberStart` 为空时回退显示 `paperOrder`（`static/papers.js` 的
   `showUnitOrder`），所以出现 `10.`–`19.`。

结论：不是审核顺序或上传顺序错了，而是**上传数据没有把原卷题号带进试卷档案**；
平台也不会按单元顺序自动推断原卷题号（这是正确行为，避免把上传单元序号当题号）。

## 改了什么（线上数据修复）

服务端 `~/mudu-quiz` 的 `data/mudu.sqlite3`，试卷 `legacy-4b94e44e16825a270700d29f`
（标题「2024年安徽省初中学业水平考试」，34 个单元）：

- 只给 20 个独立题单元补 `number_start`/`number_end`：
  `q-1..q-5` → 1–5，`q-21..q-30` → 21–30，`q-76..q-80` → 76–80。
- 书面表达 `q-81` 保持 `NULL`/`NULL`（无印刷数字题号）。
- 题组单元（6–7、8–10、11–15、16–20、31–40、41–50、51–55、56–58、59–62、63–65、
  66–69、70–72、73–75）原范围不动。
- 修复前备份：`~/mudu-quiz/backups/mudu-before-2024-paper-numbers-20261112T112643.sqlite3`
  （60,481,536 字节）。

## 没改什么 / 边界

- 不改本地批次数据、不改上传载荷、不改 `07_upload` 冻结回执。
- 不按上传顺序推断题号；只填结构确认阶段已存在的原卷题号。
- 不改题组范围、不改题目内容/答案/解析/附件。
- 没有改 `papers.verified`（仍是 legacy、未核对），没有上传、发布或删除任何试卷。

## 如何验证

1. 修后 `paper_items` 覆盖 1–80 共 80 个唯一题号、无缺号；`q-81` 仍为 NULL。
2. 用服务端 `backend.paper_detail(db, "legacy-4b94...", admin_actor)` 复验：
   `q-21` 返回 `numberStart=21, numberEnd=21`，`q-81` 返回 `None/None`。
3. 前端 `static/papers.js::paperNumberLabel` 在 `numberStart` 非空时输出 `21.`，
   不再走 `unitOrderLabel` 的 `10.` 回退。

## 回退方式

用修复前备份覆盖 `~/mudu-quiz/data/mudu.sqlite3`（先停 `mudu-quiz` 服务）后重启即可。

## 后续（已做）

已采用「上传载荷在 `location` 写入真实『第 N 题』」的方案，让 legacy 整卷档案可推断：
`app/workflow/upload_payload.py` 对空 `location` 的独立题补 `第N题`，书面表达与
scoped 课时批次豁免。详见
[2026-11-12-upload-paper-number-location.md](2026-11-12-upload-paper-number-location.md)。
不改变「审核差异」与上传边界。
