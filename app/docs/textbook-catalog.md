# 教材目录（课时同步练习）

`references/textbook-catalog-1.0.md` 收录人教版、外研版、牛津译林版初中英语新教材目录：3 个版本、18 册、128 个单元（含 Starter）、781 个课时节点，与远端 `/api/catalog/textbooks` 和 `/api/catalog/curriculum-nodes` 对齐。`references/textbook-catalog.json` 是它的结构化产物，由脚本生成，不要手改。

## 当前状态：同步练习自动匹配已启用

- **适用来源**：仅同步课时类来源（`同步练习`、`单元测验`）在 `knowledge` 阶段注入教材目录并匹配 `textbook`；真题、模拟卷等非同步来源不触发教材匹配。
- **两级挂载**：
  - 明确属于具体课时的题目：填写 `version` / `book` / `unit` / `section` / `evidence`，上传映射为 `7年级上册 · Unit 1 You and Me · 第一课时 Section A (1a-1d)`。
  - 与课时平级但不属于具体课时的单元级版块（`单元话题写作`、`综合阅读提升`、`Reading Plus`、`单元测验`、`单元复习`）：**省略 `section` 直接挂在具体单元下**（上传映射为 `7年级上册 · Unit 1 You and Me`），禁止顺延挂到上一课时。
- **缺证据留空放行**：原卷确实缺少教材版本或单元线索时允许省略 `textbook`，不编造、不阻断流程与上传。
- **上传映射**：`prepare-upload` 在批次含 `textbook` 时自动拉取远端 `/catalog/textbooks` 与 `/catalog/curriculum-nodes`，将本地 `textbook` 确定性映射为 `textbook_id`、`curriculum` 与 `textbookMappings[]`（复用通用知识点版本 `KCV-ENGLISH-KNOWLEDGE-1-1` 与本题 `knowledgeIds`）。

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

## 独立工具规则（当前不接入知识点流程）

- 版本、册次、单元、Section 名称必须与目录**完全一致**；不做模糊匹配，找不到就是找不到。
- 独立候选校验要求 `version`、`book`、`unit` 必填，`section` 可省。`evidence` 必须是非空字符串数组，例如 `"evidence":["正文 L1-L2：原卷所示教材单元标题"]`，其中位置与文字必须来自真实来源；示意文字不可当作真实证据。
- 独立工具拒绝单个字符串、空数组、名称对不上、册次与版本不一致或缺少证据的候选。该工具不在当前知识点导入链路中；自动知识点导入只省略本地教材字段并保留原候选。恢复前仍须同步任务规则、示例与校验器，不自动包装真实候选来规避检查。

- 校验器会从精确目录补齐 `grade/topic`；规范化结果必须能再次校验。提供 `grade` 时须与目录年级完全一致，否则拒收；不得把目录派生字段误判为模型多填字段，也不得以此触发同输入模型重试。

## 边界

本目录是**教材课时结构**，不含教材版知识点 ID。远端 `textbookMappings` 需要服务器的 `textbookId` 与教材版知识目录，当前 `fetch_catalog` 只拉通用版；因此本地产出的 `textbook` 是课时归属候选，挂远端章节目录仍需用服务器实际 ID 映射，不能拿本地名称冒充 ID。
