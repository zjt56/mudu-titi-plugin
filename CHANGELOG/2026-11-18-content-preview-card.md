# 审核页新增「文章大意 · 解析」只读预览卡

## 背景

试卷审核页左栏只有「原卷预览」，文章大意与解析只能点开「原卷修改」弹窗才能看到；审核员核对解析是否缺失、是否串材料（规则 22/35）时要来回开关弹窗，且弹窗是编辑态，容易误改。

## 改了什么

- `lib/client.js` 新增 `contentPreviewModel(detail)` 与 `ContentPreviewCard`，挂在左栏「原卷预览」卡片之后、右侧标签审核区之外；按 unit 重挂载，切题不复用上一题折叠状态。
- 数据只读 `detail.item`（与「原卷修改」同一份 candidates 草稿优先的内容），不新增接口、不回写：
  - 文章大意取 `item.articleSummary`；
  - 整组共用解析取 `item.explanation`（听力稿「听力录音材料：…」走这里）；
  - 逐题解析取 `item.subquestions[].explanation`，题号/答案取 `detail.subquestions[]` 的 `display_number`/`answer`。
- 口径：`''`/`无`/`未提供`/`未填写`/`N/A` 整串占位按缺失展示，不把「无」当正文；书面表达显示「不要求文章大意与解析」而不是缺失（规则 23）；无印刷题号显示「—」并注明原卷未标小题号，不补号（规则 27/34）；解析与唯一小题解析逐字相同只显示一次；整题解析与逐题解析分块，独立题答案挂在解析块标题。
- 交互只有折叠：长文 6 行（逐题 3 行）截断 + 「展开全文（N 字）」，头部「全部展开/收起」，解析缺失折叠为「无解析 N 条 + 显示」；按钮带 `aria-expanded`。
- 样式新增 `.wf-content-*`，全部走现有 token（`--wf-brand*`、`--dsw-alias-*`），无新色、无横向滚动、`overflow-wrap:anywhere`。

## 没改什么 / 边界

- 不新增后端字段、不改 `detail()` 契约、不写批次数据与 candidates 草稿。
- 不在预览卡里编辑；编辑仍只在「原卷修改」，避免两套写入口。
- 不生成、不改写文章大意与解析；占位与缺失按规则展示，不伪造内容。

## 验证

- 新增 `backend/review-content-preview.test.cjs` **11 项通过**：占位识别（整串才判缺失）、组题/独立题模型、听力整组解析不被逐题吞掉、逐字相同去重、无号题「—」、书面表达豁免、挂载位置与按题重挂载、只读断言（无 `jpost`/`jget`/`onChange`/输入控件）、样式类存在。
- 全量后端：`node --test backend/*.test.cjs backend/*.test.mjs` → **433 项，431 通过、0 失败、2 跳过**。
- `node --check lib/client.js` 通过。
- 渲染检查：用真实组件代码 + 真实 `.wf-content-*` 样式生成静态页，覆盖完形组（大意 + 10 条解析 + 1 条缺失）、听力长对话组（590 字听力稿折叠/展开）、无号题组、独立听力题、书面表达、全空、320px 窄栏七种形态；题号/答案 chip 在浅色主题下补了中性描边，确认无溢出、无横向滚动。

## 回退

删除 `ContentPreviewCard`/`contentPreviewModel` 及挂载行、`.wf-content-*` 样式与 `TEXT.contentPreview`，去掉 `backend/review-content-preview.test.cjs` 即可回到改动前；不涉及批次数据与上传边界。
