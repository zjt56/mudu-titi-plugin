# 教材目录（课时同步练习）

`references/textbook-catalog-1.0.md` 收录人教版、外研版、译林版初中英语新教材目录：3 个版本、18 册、128 个单元（含 Starter），以及各单元 Section 与课时条目。`references/textbook-catalog.json` 是它的结构化产物，由脚本生成，不要手改。

## 当前状态：自动匹配暂停

2026-11-11 用户要求先只匹配知识点标签，教材尚未就绪。所有来源的知识点任务均不提供教材索引、不生成教材位置；旧候选的本地 `textbook` 在正常验收时省略，组装不传播。知识点目录与证据校验继续有效。目录数据及以下独立查询、校验工具保留供后续建设，不能据此重新启用自动教材匹配。

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
