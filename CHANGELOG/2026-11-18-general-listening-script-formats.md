# 听力录音稿通用格式识别

用户要求后续不同试卷的听力录音材料按通用口径写入对应解析。识别中文听力稿/录音稿和英文 listening scripts/tapescript 标题，中文、罗马数字等大题标题，中英文题号范围、括号及全角题号；没有大题标签时只在题号或完整范围唯一对应时匹配。非权威来源副本不竞争；同名题型存在多个无编号题组、重复题号、来源不一致或范围不完整时不猜。解析写入和来源校验继续共用相同来源绑定，不修改历史批次。

验证：`workflow.test_listening_script workflow.test_listening_material_integrity workflow.test_upload_content` 66 项通过；用户提供的 2018 年原卷 19 个听力单元只读匹配 19 个。覆盖多种标题/编号、无大题标签的明确范围、错误范围、重复题号、冲突原稿、已有解析保留及上传投影。实际批次交付及运行中插件更新尚未验证；未知格式不保证自动识别。

回退：还原本次 listening_script.py、test_listening_script.py 变更，不改用户批次。
