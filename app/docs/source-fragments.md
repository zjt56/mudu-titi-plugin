# 解析阶段的原文片段引用

`workflow.content_rules.validate(payload, units, typed, batch)` 支持解析阶段的可选输入语法：组级/独立题的 `explanation`、`articleSummary` 以及子题的 `explanation`，可以使用原有字符串，也可以使用严格的 `source_ref` 对象。引用只是由程序搬运原文的方式；解析归属、题号/材料唯一绑定、占位、AI 生成限制、题组与子题覆盖、evidence 等现有校验全部继续执行。正式产物仍是字符串，下游无需解析引用。

例如来源文件第二行为 `1.A 【解析】第一题解析。`，可填写：

```json
{
  "source_ref": {
    "path": "01_source/附录.txt",
    "sha256": "<该来源整个文件原始字节的64位小写SHA256>",
    "start_line": 2,
    "end_line": 2,
    "start_column": 8,
    "end_column": 14
  }
}
```

程序展开为 `第一题解析。`，不会让模型重抄、推断或重新撰写文本。模型按显示宽度给出的 `end_column` 超出实际末行时按末行末尾截断；片段开头的 `【解析】`/`【答案】`/`【文章大意】` 等字段标题按字段语义去掉。`31.C　` 等题号/答案前缀仅在紧跟明确解析标题时移除；普通编号和小数正文保持原样。展开后仍须通过原有题号归属与原文一致性校验。

- `path` 是批次相对路径。仅允许现有来源检查器的 `01_source/附录.txt`、`01_source_ans/正文.txt`、`01_source/正文.txt` 和 `01_source/materials/attachment_NNN.txt`（数字至少三位）。拒绝绝对路径、路径穿越、任意其它文件、批次内软链接，以及材料角色中 `content_authority=false` 的参考副本。
- `sha256` 绑定完整来源文件的原始字节，包含 BOM 和换行符。文件变化后旧引用无效；不得改填一个猜测的摘要。
- `start_line`、`end_line` 是必填整数，从 1 起，包含首尾行；布尔值、字符串数字和小数均无效。
- `start_column`、`end_column` 是可选整数，按 Unicode 字符从 0 起计数；分别作用于首行和末行，末列不包含在片段中。默认从首行开头到末行文字末尾，不计该末行的换行符。它们不是字节偏移，也不是 JavaScript UTF-16 码元偏移。
- 多行引用保留中间原始 LF/CRLF、缩进和空白；UTF-8 BOM 不进入文本。空片段、反向坐标、行号越界和起点列越界仍拒绝；末列超过末行长度按末行末尾截断。对象不接受额外字段。
- 引用仍需提供原有非空 `evidence` 列表。程序向已有 evidence 追加路径、行列范围与源文件摘要；不靠自动证据修复缺失/空列表。引用不可声明内部的确定性直抄 evidence 标记，也不能借它跳过归属校验。

## API 与提示词接入

`workflow.content_fragments.source_index(batch)` 返回：

```json
{
  "format": "source-fragment/v1",
  "sources": [{
    "path": "01_source/附录.txt",
    "sha256": "<完整来源文件摘要>",
    "lines": [{"number": 1, "text": "【文章大意】原文大意。"}]
  }]
}
```

该索引使用当前允许的权威来源，包含所有实际行且不静默截断；行文字不含 BOM 和行尾换行符。提示词构建方可按任务挑选来源/行，但必须保留真实行号与完整文件摘要，明确说明省略范围。索引不宣称片段已通过题目归属校验。

`workflow.content_fragments.expand_references(rows, batch)` 是校验器使用的内存展开 API；返回是否存在引用，所有引用的路径/摘要/范围检查成功后才统一替换字段。调用它本身不代表校验通过，不能代替 `content_rules.validate`。引用与旧字符串可混用，展开后再执行原有校验。

提示词/schema 集成由调用方处理：只对 explanations 阶段上述三个位置使用 `string | source_ref` 联合类型，`source_ref` 外层与内部均禁止额外属性；其它阶段仍沿用字符串。将来源索引摘要和行号与来源原文一起提供给模型，要求只定位现有解析/大意，确认不存在时仍返回字符串 `无`。正式解析产物保存校验器返回的字符串。

验证：从 `app/` 运行 `python3 -m unittest workflow.test_content_fragments workflow.test_content_labeling workflow.test_upload_content`。
