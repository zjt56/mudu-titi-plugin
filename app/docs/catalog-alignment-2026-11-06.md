# 题库目录与标签对齐报告（2026-11-06）

对比对象：远端题库只读接口 + 静态目录，本地 `app/references/`。
结论：**知识点、教材目录、题型、难度、年份/地区等已对齐；本次修复了来源标签、来源/题型别名、教材 10 个 Project 单元三处缺口。**

## 1. 远端接口与本地文件

| 远端 | 本地 |
| --- | --- |
| `GET /api/catalog/knowledge-versions` | `references/knowledge-catalog.json`（version/versionId） |
| `GET /api/catalog/knowledge-points?version_id=` | `references/knowledge-catalog.json`（points） |
| `GET /api/catalog/textbooks` | `references/textbook-catalog.json`（18 册） |
| `GET /api/catalog/curriculum-nodes?textbook_id=` | `references/textbook-catalog.json`（units/sections/items） |
| `GET /filter-framework.json` | `references/filter-framework.json` |
| 后端常量 `SOURCE_TYPES` / 别名表 | `references/filter-framework.json` + `workflow/upload_payload.py` |

## 2. 知识点目录

| 项目 | 本地 | 远端 | 结果 |
| --- | --- | --- | --- |
| 版本 | 英语知识点目录1.1 / KCV-ENGLISH-KNOWLEDGE-1-1 | 同 | ✅ 一致 |
| 节点数（active） | 522 | 522 | ✅ |
| 完整路径集合 | 522 | 522 | ✅ 0 差异 |
| 同路径 ID | 522/522 相同 | | ✅ |
| 重复路径 | 无 | 无 | ✅ |

## 3. 教材目录

| 层级 | 本地 | 远端 | 结果 |
| --- | --- | --- | --- |
| 教材版本 | 3 | 3 | ✅ |
| 册 | 18 | 18 | ✅ 册名全部一致 |
| 章/Unit | 138 | 138 | ✅（修复后） |
| 一级 Section | 660 | 660 | ✅ 名称全部一致 |
| 二级页面条目（如 1a-1b） | 449 | 449 | ✅ 名称全部一致 |

修复前：牛津译林版 5 册（七上、七下、八上、八下、九上）各缺 2 个 Project 章，共 10 个；已按远端节点补入本地目录，`counts.units` 由 128 更新为 138，Section/页面条目数量不变。Markdown 源文件 `textbook-catalog-1.0.md` 与生成器 `build_textbook_catalog.py` 同步更新：顶层 Project 作为一级单元参与生成，不再落到 `extras`。

## 4. 标签对齐

| 标签 | 本地 | 远端 | 结果 |
| --- | --- | --- | --- |
| 题型 | 27 | 27 | ✅ 名称与顺序一致 |
| 题型子类 | 与题型绑定 | 同 | ✅ |
| 难度 | 容易/适中/困难 | 同 | ✅ |
| 来源 | **13** | 13（后端常量） | ✅（修复后） |
| 年份/地区/年级/学期 | 与远端静态一致 | 同 | ✅ |
| `type-knowledge-map.by_type` | 27 | 27 | ✅ 无缺项/多项 |

来源修复：本地原为 11 种，缺远端后端的 **精品题源、课本原创**；已补入 `filter-framework.json`。

别名修复（按服务端 `SOURCE_UPLOAD_ALIASES` / `QUESTION_TYPE_ALIASES`）：

| 输入 | 归一化 |
| --- | --- |
| 精品资料 | 精品题源 |
| 历年考题 | 中考真题 |
| 模拟卷 | 中考模拟 |
| 单项选择 | 单项填空 |
| 短对话理解 / 长对话理解 / 短文理解 / 信息转换 | 对应听力题型 |

别名在标签审计与线上字段输出前统一归一，避免"本地能过、远端才识别"或反过来的偏差。

## 5. 仍存在的非阻断差异

1. 远端静态 `/filter-framework.json` 仍是 11 种来源，而后端代码 `SOURCE_TYPES` 是 13 种。插件已按后端行为对齐；建议服务端把静态文件也同步为 13 种。
2. 本地上传映射对 `单项选择` 等旧称已做归一；正常流水线在抽取阶段也会归一，属于双保险。
3. `模拟卷` 归一为 `中考模拟` 后，远端仍要求 `institution/school/regional_version`，这是业务必填，不是目录差异。

## 6. 验证

```text
知识点评测：522/522，路径与 ID 全同
教材：18 册 / 138 章 / 660 Section / 449 页面条目，与远端逐项一致（含顺序）
生成器 --check：一致 {versions:3, books:18, units:138}
来源：13/13
题型：27/27
回归：Python 154 项通过；Node 15 项通过
```
