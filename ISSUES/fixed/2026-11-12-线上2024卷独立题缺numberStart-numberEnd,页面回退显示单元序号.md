# 卡点：线上2024卷独立题缺numberStart/numberEnd，页面回退显示单元序号

- id: 2026-11-12-线上2024卷独立题缺numberStart-numberEnd,页面回退显示单元序号
- status: fixed
- batch: 【木牍教育】2024中考英语试卷
- batch_dir: workflows/【木牍教育】2024中考英语试卷
- stage: upload
- check: PAPER_NUMBER_RANGE_MISSING
- created: 2026-11-12T11:29:31+08:00
- last_seen: 2026-11-12T11:29:31+08:00
- occurrences: 1
- fixed_at: 2026-11-12T11:29:37+08:00
- fix_commit: server-db repair (no repo commit)
- verification: paper_items覆盖1–80共80个唯一题号无缺号；backend.paper_detail复验q-21→21/21、q-81→None/None；备份backups/mudu-before-2024-paper-numbers-20261112T112643.sqlite3
- title: 线上2024卷独立题缺numberStart/numberEnd，页面回退显示单元序号

## 现象（卡点）

单项填空排第10–19单元却显示10.–19.（应为21.–30.）；听力1–5、单词拼写76–80同样为空；书面表达应留空

## 具体检查与判定

- 失败检查：PAPER_NUMBER_RANGE_MISSING
- 判定口径：上传载荷把number/numbers/numberStart/numberEnd当本地记账剥离，远端questions无题号字段；legacy试卷档案只从子题号或location“第N题”推断，独立题location为空→NULL
- 修复方向：按结构确认的原卷题号给20个独立题补1–5/21–30/76–80，书面表达保持null；备份后写paper_items并复验1–80全覆盖

## 修复记录

- 修复方式：服务端备份后修复 legacy-4b94e44e 的 paper_items：20个独立题补1–5/21–30/76–80，书面表达保持NULL；题组范围与题目内容未动
- 修复提交：server-db repair (no repo commit)
- 复验证据：paper_items覆盖1–80共80个唯一题号无缺号；backend.paper_detail复验q-21→21/21、q-81→None/None；备份backups/mudu-before-2024-paper-numbers-20261112T112643.sqlite3
- 标记时间：2026-11-12T11:29:37+08:00
