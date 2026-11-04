# 教材目录（课时同步练习）

`references/textbook-catalog-1.0.md` 收录人教版、外研版、译林版初中英语新教材目录：3 个版本、18 册、128 个单元（含 Starter），以及各单元 Section 与课时条目。`references/textbook-catalog.json` 是它的结构化产物，由脚本生成，不要手改。

## 用途

只用于**课时同步练习类来源**（`同步练习`、`单元测验`）：这类原卷的标题或章节写明所属教材单元，录题时需要把题目挂到对应教材课时上。

中考真题、期末/期中/月考、原创改编等普通试卷**不做教材匹配**；打标签子智能体2 在这些来源上不会收到教材索引，也不应输出教材位置。

## 生成与校验

```bash
python3 scripts/02-structure/build_textbook_catalog.py          # 生成 JSON
python3 scripts/02-structure/build_textbook_catalog.py --check  # 校验与 Markdown 一致
```

解析只读「完整教材目录树」小节；译林版与单元并列的 `Project 1/2` 收进册次 `extras`，不算课时单元。

## 查询

```bash
python3 -m workbench.textbook units --version 人教版 --book 人教版七年级上册
python3 -m workbench.textbook resolve --version 人教版 --book 人教版七年级上册 --unit "Unit 1 You and Me" --section "Section A"
python3 -m workbench.textbook index        # 给模型的紧凑索引
```

## 匹配规则

- 版本、册次、单元、Section 名称必须与目录**完全一致**；不做模糊匹配，找不到就是找不到。
- 打标签产物里的 `textbook` 必须带原文证据；`version`、`book`、`unit` 必填，`section` 可省。
- 名称对不上、册次与版本不一致、缺少证据都会被导入器拒绝。

## 边界

本目录是**教材课时结构**，不含教材版知识点 ID。远端 `textbookMappings` 需要服务器的 `textbookId` 与教材版知识目录，当前 `fetch_catalog` 只拉通用版；因此本地产出的 `textbook` 是课时归属候选，挂远端章节目录仍需用服务器实际 ID 映射，不能拿本地名称冒充 ID。
