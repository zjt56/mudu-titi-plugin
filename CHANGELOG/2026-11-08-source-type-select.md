# 2026-11-08 审核修改标签的来源类型改为下拉选择

## 背景

用户要求试卷审核“修改标签”中的来源分类（中考真题、中考模拟等）从提供的选项选择，而不是手动输入。

## 改了什么

`lib/client.js::LabelModal` 将 source_type 的 input 改为 select，选项读取已有 `tax.sources`，与上传页及后端 taxonomy.sources 使用同一来源目录。保留“未选择”空值；历史值不在目录时以禁用选项显示原值，避免打开弹窗就静默改掉数据，用户可重新选择合法项。加上来源类型 aria-label，保留原控件样式和保存回调。

## 边界

只改来源类型控件，不改下面具体卷名的“来源”文本框，不改题库目录或批次数据；未自动保存任何题目。

## 如何验证

- node --check lib/client.js 与 lib/index.js 通过。
- taxonomy 接口提供 13 个选项：中考真题、中考模拟、中考复习、期末试题、期中试题、月考试题、开学试题、单元测验、同步练习、木牍原创、木牍改编、精品题源、课本原创。
- Impeccable detector 返回空问题列表。
- 已安装 md-upload-tiku packageDir 解析到当前工作区，安装路径下 client.js 已包含该 select。
- 浏览器工具报告无可启动 Electron，未完成真实弹窗点击验证，不安装额外浏览器环境。界面重新加载插件客户端后生效。
- Node 全量 110 项：108 通过、2 失败；使用 git archive HEAD 的临时基线同为 108 通过、2 失败，失败均为既有选项图片网格/题干图片矩形样式断言，无新增失败。

## 回退方式

将 LabelModal 的 source_type select 恢复原 input 即可，不需要迁移用户数据。
