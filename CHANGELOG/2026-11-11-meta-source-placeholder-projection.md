# meta 投影清除来源旧占位，不放宽最终上传校验

## 背景与触发

批次 `01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio` 的最终格式报告有 119 项错误：27 单元的真实顶层 source/source_type 与 filterMetadata 的「未提供」冲突（54 项），27 父题与 19 子题的显式空 year 被拒绝（46 项），19 子题显式 source_type=未提供 不在目录（19 项）。已识别 metadata 有课时来源但没有年份证据，不能为放行推断年份。

## 改了什么

- `app/workflow/metadata_policy.py`：新增纯函数 `project_source_metadata`，深拷贝父/子题，在 meta 上传视图投影中移除 source/source_type/year 的 None、空白和「未提供」。仅当一方别名缺失时同步现有另一方；数字年份转整数。两方均为实值而冲突时原样保留，由现有上传硬校验拒绝。
- `app/scripts/05-meta/assign_meta.py`：正常 meta 命令生成 batch_meta.base.json 前使用该投影。原输入 batch.json 不由脚本改写；正常 underlines 消费投影后生成最终视图。
- `app/workflow/test_meta_source_projection.py`：新增 5 项测试，实际运行 meta → underlines → format_gate 与 build_payload 消费链，覆盖父/子占位、未知年份省略、有效来源/年份保留、真冲突和真无效值拒绝、输入不变和幂等。

## 边界

未改 wire_format 或 upload_payload 的硬校验，未从父题、批次名或日期猜年份/题源，未更改题文、知识标签、教材暂停口径或上传权限。实现与测试仅写临时夹具；主线程随后通过正常 meta/underlines/validate/review/format 命令刷新真实派生数据，没有手写 manifest、正式题目或审核结论。未上传。此次正常续跑另暴露来源占位变化误触发重审的问题，独立登记并修复，不能宣称原审核文件保持字节不变。

## 验证

设置 PYTHONDONTWRITEBYTECODE=1、PYTHONPATH=app、PYTHONUTF8=1、PYTHONIOENCODING=utf-8 后运行：

`python -X utf8 -m unittest workflow.test_meta_source_projection workflow.test_metadata_source_name workflow.test_upload_payload workflow.test_format_gate`

106 项通过，耗时 19.195 秒。Windows 默认 GBK 及子进程编码造成的既有测试读写编码问题通过 UTF-8 运行环境消除，无须修改旧测试。测试仅写临时夹具目录。

## 真实批次正常复验

2026-11-11 17:33 至 17:41，正常运行 meta、underlines、validate 及下游闸门：本地体检 19/19，通过最终 format_gate，119 项错误归零；27 单元、41 小题、7 个附件。知识点目录快照、远端媒体与冻结校验仍是实际上传准备时的延期检查，不代表已上传或线上就绪。

外部只读证据 `C:\Users\Administrator\AppData\Local\Temp\lesson-meta-repair-evidence\final-observed.json` 对照修复前快照：正文、confirmed、types、answers、knowledge、batch 六份正式文件 SHA 全部不变。meta 差异限定 source 65、source_type 65、year 46 和既有 meta 命令新增的 46 处缺省 difficulty=适中；没有覆盖既有非空难度值。不能写成 meta 只变化来源字段。

原两轮审核文件因误触发重审发生变化；原 SHA `cbc5cdc558692e87d1104ffaad572db32cd80be92884ec2af847061694612c36` 在 `workflow/review-history/9258fb42c46c4ccb9a57c2acf6cba3cc.json` 中逐字保留。新轮产生 12 项差异，旧 11 项的活动队列保留问题另行修复；不得把原保护脚本的失败报告为通过。

## 回退

仅撤回本记录列出的 meta 调用、纯投影函数及新增回归测试；不回滚并行人员的修改或真实批次数据。
