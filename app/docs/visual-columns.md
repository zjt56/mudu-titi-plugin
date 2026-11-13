# 整页视觉栏目归属与无印刷题号的实际题

栏目名称不能单独决定题目身份。基础巩固、栏目提示、熟词生义、创新训练、拓展设问等可表现为文字、图片横幅、边框或旁栏；同一名称在不同位置可能是容器、说明、独立作答任务或某题附加设问。

## 证据与执行

结构确认前 `runner._ensure_visual_evidence` 调用 `visual_columns.build`：

1. `source_pages.prepare` 优先读取当前Word原件，渲染全部页面；PDF原件可使用真实完整页预览。原件、正文、页图快照与摘要保存在 `01_source/visual-pages/manifest.json`，包含页序、实际尺寸与缺字体警告。嵌入小图绝不是整页证据。
2. 使用当前明确选定的Harness视觉模型核对全部页和带行号的Word文字。多页可使用无损并排总览（保留原页尺寸和页码），bbox仍引用原页坐标，不重排正文/OCR阅读顺序。超过完整窗口预算不截断伪称成功，返回具体诊断。
3. `01_source/visual-columns.json` 保存每个栏目/题目区域的 `column_id`、原栏目名称、role、parent_id、正文start/end、可选真实heading_line、实际printed_numbers、页面bbox和判断理由。首尾quote必须逐字对应当前正文行；父子范围、题窗口重叠、页图与原件变更均严格拒绝。
4. 结构模型消费当前有效栏目证据，按原文生成单元，正式闸门仍复验全文覆盖、材料完整、题干连续切片及身份。不因视觉返回JSON就自动通过。

## 角色与无号身份

- container：栏目/板块容器，独立计数为0，其下可以有题或说明。
- guidance：考点提示、栏目说明、范例等，不因名称或一串数字就自动成题。
- question：有实际作答任务的独立题或共享材料题组；题型与group/standalone仍由结构模型结合内容判定。
- extension：有版面与内容证据证明依附父题的附加设问；没有父节点的extension拒绝。独立的拓展栏目实际题必须标question，不得一律挂到前题。

真实无号实际题用 `printed_numbers=[]` 和 `source_span.number_binding=unnumbered_section`，由column_id、当前原文窗口与整页证据绑定。内部兼容numbers只供既有管线建立question_id，不能显示/上传成原卷印刷号。上传范围仍经现有唯一投影复建证明后给null/null，不因空数组直接放行。未知的无号子题远端契约不在本改动中放宽。

题目来源内保留 `source_section.column_path`（root到最窄归属的栏目链），有号单元也可记录，不为新增归属重编已有身份。结构、下游来源重建和预览消费同一证据；答案必须另行证明其栏目/大题归属与原文，视觉归属本身不能证明答案值或缺失。

## 渲染配置与缓存

自动使用PATH中的官方 `dsoffice`，或从DSH运行环境解析官方 `@deepseek-ai/libreoffice-kit` 的公开API。可用 `MUDU_SOURCE_PAGES_CONFIG` 指向JSON显式command renderer：

```json
{"command":["renderer","--input","{input}","--output-dir","{output_dir}","--dpi","{dpi}"],"dpi":144,"timeout_seconds":180}
```

不使用shell、不安装软件、不读凭据。显式配置失效不静默换渲染器。自定义输出需证明full_page=true、source_sha256及完整1..page_count页覆盖。当前缺字体记录警告供审计，不改Word文字。

同原件/正文/页图、实际路由及提示版本复用栏目结果。失败短暂延期，返回unavailable与原因，不能消耗旧证据继续猜。历史批次不全量改写；测试用隔离副本，不修改已归档原件或批准绑定。
