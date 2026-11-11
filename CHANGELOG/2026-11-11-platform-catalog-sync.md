# 平台侧题型目录登记 `完成句子/补全句子`

## 背景

2026-11-11 桌面《英语题型汇总.xlsx》表一把 5 个看图补全句子由 `单词拼写/看图填词` 改判为
`完成句子/补全句子`，插件本地目录（`app/references/filter-framework.json`）已补入 `补全句子` 并随
`569029f` 落地。但平台侧题库目录 `filter-framework.json` 的 `完成句子.subtypes` 仍是旧六项，
上传时平台 `validate_upload_question_types` 会返回 `invalid_question_subtype`，本地体检 A7 也会报
「子类不属于完成句子」。用户 m06231 要求「平台侧也加上这个」。

## 改了什么（平台服务器 `/home/ubuntu/mudu-quiz`）

| 文件 | 改动 | 备份 |
|---|---|---|
| `static/filter-framework.json` | `完成句子.subtypes` 追加 `补全句子`（姿态保持 `version=1.1`、27 主类不变） | `backups/filter-framework.json.bak-20261111-192506` |
| `static/docs/index.html` | 「题型主类与子目录」表中 `完成句子` 一行补 `、补全句子` | `backups/docs-index.html.bak-20261111-192528` |
| `static/app.js` | 目录请求缓存戳 `v=20261023-filter-framework-1` → `v=20261111-filter-framework-2`，避免浏览器继续用旧目录渲染下拉 | `backups/app.js.bak-20261111-192701` |

哈希：`filter-framework.json` `2368f327…463ae` → `15548fbb…79e16d`；`docs/index.html`
`96acff0e…b91c4` → `8a0492c8…bb894`；`app.js` `9accf344…e405a` → `2ad3dcd0…4422`。

## 没改什么 / 边界

- 未改任何题目数据、未发布题目、未动审核队列；平台目录 `version` 与 `source` 保持原值。
- 未改旧发布快照 `releases/review-f58f782/static/filter-framework.json`（回退时以 `backups/` 为准）。
- 平台 `filter-framework.json` 仍是权威目录的一份拷贝：以后改题型目录必须**本地与平台两侧同时登记**，
  只改本地会让 A7/平台校验拒收。

## 如何验证

- `curl http://127.0.0.1:18901/filter-framework.json` 与 `http://127.0.0.1:89/filter-framework.json`
  均 200，`sha256` 与磁盘文件一致（`15548fbb…79e16d`），`补全句子` 出现 2 处（`任务型阅读` 与 `完成句子`）。
- 按 `backend.py:3980 validate_upload_question_types` 的同一口径读取目录：`完成句子` 子类集合含
  `补全句子`，`任务型阅读/补全句子` 仍在，`完成句子/汉语提示` 仍在 → 该子类不再被判 `invalid_question_subtype`。
- `validate_upload_question_types` 每次调用都重新读目录文件，因此**无需重启 `mudu-quiz.service`**；
  已确认 `curl http://127.0.0.1:89/app.js` 返回新缓存戳。

## 回退方式

```bash
cd /home/ubuntu/mudu-quiz
cp backups/filter-framework.json.bak-20261111-192506 static/filter-framework.json
cp backups/docs-index.html.bak-20261111-192528 static/docs/index.html
cp backups/app.js.bak-20261111-192701 static/app.js
```

回退同样不需要重启服务；回退后平台会重新拒绝 `完成句子/补全句子`，插件侧应把看图补全句子退回
`单词拼写/看图填词` 或按用户裁决改投 `情景运用`，不得静默改回。
