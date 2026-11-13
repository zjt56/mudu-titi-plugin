# 任务型阅读表格作答空位与A9来源证明

## 背景与触发

12课时Starter Unit3第四课时Project的答案超时卡点修复后，22条原答案已正式交付。继续汇总时sec-7f100795c4cef82a-u43的6–10被A9判为stem为空。只读核对：原文TABLE2中五个编号作答空位完整，当前题目的materialBlocks仍含完整表格，每个子题的答案、question_id和来源行号存在。不能凭stem空值推断丢题，也不能造题干放行。

## 改了什么

新增table_structure只读证明：当前确认结构、原文窗口、原生source_ref/table_number与question_id源行逐项一致，完整表格/编号唯一性/全部子题及选项保持原文才认可对应空stem。question_content返回精确空位集合，check_structure与publish_check共用；普通空题、缺答案、表格/正文丢失、错scope或裸候选保持严格阻断，不按题型或空printed_numbers猜豁免。

首次静态候选precheck通过，正式合流仍失败。进一步沿真实batch_fill→try_assemble→accept_result定位第5子题F选项被来源恢复管道将换行改为br，与草稿的换行严格比较不等。最终仅本proof选项比较精确等价br与换行，不删除其它字符/标点，拒绝未知类型/缺text/额外非选项/标签乱序。未改候选数据。

此证明仅针对原表格作答空位，不证明其它材料布局或选项归属语义正确；原审核仍须真实执行，实质差异不自动裁决。当前第5子题末选项包含后续任务二/表格的既有投影保留原证据，不通过本补丁自动重排，也不宣称它语义正确。

## 验证与现场

相关Python60项：56通过、4跳过、0失败；新专项8项含真实托管initialize→四路sync→try_assemble→validate_only/import两次完整precheck，及换词/截断/缺失/错号/裸数据反例。临时真实拷贝真实try_assemble=0，来源和payload保留，精确集合{6,7,8,9,10}，均TABLE2/正文52行。

首次正式assemble失败证据保留；最终修复后正式runner assemble --force于2026-11-13 07:05:45通过、model_calls=0。meta和underlines随后完成；validate暴露独立的原卷[跨学科]标记未保留D27/B9，另立open处理，没有因本补丁掩盖。表格卡点已经原检查复验后移入ISSUES/fixed。Node最终全套351项349通过2跳过无失败。一次lead验证模块名test_native_unnumbered不存在导致loader error，修正为test_native_unnumbered_structure后上述60项全绿。仅当前本地批次恢复，不自动上传发布，不修改原卷或填造stem。

## 回退

回退本轮表格来源证明与两处消费者调用，保留原表格、答案、失败报告与已导入记录；不直接改manifest或删除问题记录。
