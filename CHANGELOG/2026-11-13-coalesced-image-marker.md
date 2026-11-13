# 证据里合并写法的图片标记不再当作媒体路径

日期：2026-11-13。类型：内容任务媒体绑定（校验器容错）。

## 背景与触发

批次 `【木牍教育】21年安徽中考英语` 知识点标签阶段失败：

```
{"error": "MEDIA_DIRECTORY_NOT_ALLOWED", "code": "integration"}
```

排查：`03_types/types.json` 的 evidence 里有说明文字

```
正文 L10-L11 第1题题干仅一句话,三个选项位为纯图片标记 [IMG image4/5/6.jpeg]
```

模型把三张选项图合并写成 `[IMG image4/5/6.jpeg]`。`content_media` 把它当媒体路径去绑定，
目录不在允许白名单内，于是整份知识产物被拒。

## 改了什么

- `app/workflow/content_media.py`：`_candidate` 增加判断——值里出现“数字/数字”合写且以图片后缀结尾
  （如 `image4/5/6.jpeg`、`image7/8/9.jpeg`），视为描述性合并标记，返回 `None`（不绑定媒体），不再报
  `MEDIA_DIRECTORY_NOT_ALLOWED`。真实单图引用（`image4.jpeg`）照常绑定并校验哈希；真正越界目录
  （如 `01_source/other/x.jpeg`）仍拒绝。
- `app/workflow/test_content_media.py`：新增回归——合并标记不绑定、真实单图仍绑定、越界目录仍拒绝、
  含合并标记的 context 快照能正常产出。

## 没改什么 / 边界

- 不放宽媒体目录白名单；不放行任何真实越界路径；
- 仍只使用 extraction 声明的真实媒体文件，哈希校验不变；
- 只是把“描述性合并标记”从媒体候选中排除，不修改任何题目/答案/证据原文。

## 验证

- `workflow.test_content_media`：26 项通过；
- `test_content_media` + `test_content_agent` + `test_visual_columns` + `test_source_pages` +
  `test_content_structure`：134 项通过；
- 真实批次：`knowledge` 阶段由失败转为 `completed`，产物 `04_knowledge/knowledge.json` 正常导入。

## 回退

撤回本提交即恢复严格拒绝；无需改动任何批次数据。
