# 前期校验审查与局部加固

类型候选的 `contentComplete` 必须为布尔值；最终审计也拒绝已存在的非布尔值，避免字符串 `false` 被漏判。重复检查不再重复追加相同完整性警告。音频晚到时元数据投影只剔除题目及子题的音频字段和附件，嵌套来源证据仍严格比较。

验证：`workflow.test_agent_tasks workflow.test_early_defer_policy workflow.test_delivery workflow.test_late_delivery` 共 91 项通过；`git diff --check` 通过。更广泛的 181 项中 5 项失败：调度与组装夹具仍假设旧阶段顺序、缺少当前画线视图，以及旧用例仍要求前期阻断 `contentComplete=false`；不能用该套件声称完整交接回归通过。

未修复：结构闸门无差别降级身份/归属阻断，并可能错误结案历史结构问题；旧台账下过期 meta 可能阻断独立听力投递；未提交媒体 URL 等价逻辑仅按名称可能掩盖不同图片路径。这些需要分别构造完整正反例并修复，不能以本文的局部加固宣称已解决。
