# PDF OCR 版面证据保留、课时 Markdown 识别与失败候选诊断

## 触发与核查

用户要求修复批次 `05八上人教版课时A计划Unit1HappyHoliday第五课时Section` 的 PDF-OCR 版面信息丢失及结构卡点，随后明确纠正：识别自身错序不能用针对单页的手工调行或自创重排补丁消化，应先核查官方识别参数。

旧官方 OCR 任务通过原 job ID 只读 GET 恢复：实际返回 `prunedResult.parsing_res_list` 的29个块，包括 `block_bbox/block_content/block_id/block_label/block_order/block_polygon_points/group_id`。适配器此前硬写 `blocks=[]`；提取器接入OCR页时又清空一次。原始返回还存在空内容块、跨题合并与阅读顺序错位，保存块不等于修复识别结果。

原模型产出12个结构单元，但 Markdown 标题未触发同步课时识别，跨大题重号受到全局拒绝；选择题第3题又因模型重排 stem 不能匹配连续来源片段而拒绝。原报告把这些合并为空列表，定位信息不足。

## 修改

- `paddle_jobs.py`：保存原始服务块与字段、保留服务列表次序，添加 type/text/bbox/order 别名；条件性报告坐标缺失/部分/非法。`normalize_jsonl` 支持已有原响应本地再处理。独立 `raw-result.jsonl` 递归脱敏凭据、令牌、远端URL；下载仍使用内存中的真实资产引用。
- `extract.py`：OCR版面块进入提取证据，原生PDF块另留 native_blocks，OCR坐标尺度不冒充PDF points。来源策略页统计从真实 extraction.json 读取，修正之前 pdf_unknown/0页的错误展示。
- `sync_practice.py`：仅在识别时剥除 Markdown 标题前缀，课时大题重号按 scoped-v1 处理，不改变正文原文或放宽中考卷重号。
- `structure_ai.py`：保留原模型响应、有效隔离候选、逐单元失败检查与修复范围；正式单元必须全量完整验收。相同输入先从保存响应本地复验，避免确定性失败反复调用模型。跨标题窗口仍明确拒绝；独立审阅证明裁剪标题可能丢失“每空限填一词”等真实指令，因此撤销该裁剪特例，原始标题、stem、parts完整保存在候选证据中。
- 新增真实 PDF、冻结OCR与模型响应fixture，覆盖重号身份、失败诊断、隔离而不放行和缓存复验。增加OCR适配、提取消费链回归。

## 用户纠正及当前批次

曾临时实现逐行置换投影并运行结构检查，用户随后明确拒绝这一做法。对应模块、测试、批次映射、提取集成全部撤销；正常runner重新提取原始OCR返回，再强制复验结构闸门，当前结果为 blocked，而非通过。原PDF未修改，没有直接编辑manifest制造状态。

最终保留29块版面数据和服务原Markdown次序。第3题跨栏错序仍属OCR服务识别问题；真实选项错位仍阻断，未自动上传或发布。卡点台账保持open，不能以代码测试通过冒充实批恢复。

## 官方参数核查

- 异步 jobs：https://ai.baidu.com/ai-doc/AISTUDIO/Mml7n69e7
- jobs引用的VL参数表：https://ai.baidu.com/ai-doc/AISTUDIO/2mh4okm66
- 本地Python/自部署：https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md
- 本地1.6配置（PaddleX release/3.7）：https://github.com/PaddlePaddle/PaddleX/blob/release/3.7/paddlex/configs/pipelines/PaddleOCR-VL-1.6.yaml

`useLayoutDetection` 控制版面区域检测排序；layoutThreshold/layoutNms/layoutUnclipRatio/layoutMergeBboxesMode 调节区域检测与过滤。没有已核实的独立 useReadingOrder 参数，layoutShapeMode 是框形状设置且托管jobs支持范围需另核。当前三个预处理/图表选项不是完整官方参数表，但服务实际已经做了版面检测；不能保证补传true即可修复错序。本轮未擅自更改识别参数或模型。

## 验证

- OCR/提取/结构/课时/身份联合回归：143项通过。
- 结构代理另运行协议、入口、本地代理、隔离候选128项通过（1项跳过）。
- 结构扩展测试已知作文编号警告失败在HEAD同样复现，不在本次修改范围。
- 独立审阅发现混合有效与畸形source_span的诊断生成异常，已补充dict防御与混合回归；保留原始非法span供诊断。结构focused最终124项通过。
- 实际原响应最终保留10个有效隔离单元，选择题第3题错序及末短文跨标题分别明确诊断；正式units仍为空，不以隔离成功充当完整交付。
- 全量workflow测试已尝试，但命中当前设备真实agent锁超时及既有失败，主动停止；不宣称全量通过。最终OCR/提取/结构/课时/身份联合focused为145项全部通过（20.998秒）。

## 回退

按提交中的上述通用源码与测试逐文件回退；不要覆盖工作树中不属于本次修改的自愈、上传与本机模型配置。不回退或删除用户PDF，也不改manifest为完成。
