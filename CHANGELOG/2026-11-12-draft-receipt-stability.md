# 共享草稿回执稳定性与 Windows 视觉输入编码

## 背景

批次 `05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec` 的答案任务报告 `Bundled input changed during agent task: 06_batch/question-draft.json`。逐字段复建证明，当时原件、媒体、题目对象与来源绑定不变，仅解析器代码摘要刷新导致共享草稿重写，打断持有旧字节摘要的在途任务。

后续验收发现既有图片板块证据不可用：Python 在 Windows 默认编码下写入中文路径，而 Node 固定按 UTF-8 读取，造成视觉入口 ENOENT。恢复真实板块证据后，又暴露旧结构把下一板块横幅并入上一题的边界；没有豁免来源校验。

## 改了什么

- `app/workflow/question_draft.py`：完整复建并复核所有来源/媒体摘要后，仅当旧回执自身有效、解析器键集相同、非解析器输入与全部对象/诊断完全一致时，保留原始草稿字节及真实旧解析器回执。真实来源、对象、诊断或键集变化仍重建。
- `app/workflow/visual_transport.py`：Python→Node 的 JSON 标准输入/输出显式固定 `encoding="utf-8"`，与 Windows 系统编码无关。
- 新增 `test_draft_provenance.py` 六项回归；补充 `test_visual_transport.py` 中文路径/提示词编码回归。更新 pipeline、sync-practice 文档。

## 实际复验

- 草稿与相关来源/投影回归：103/103 通过。
- 视觉传输在默认 Python 编码下：7/7 通过；UTF-8 模式下组合板块、答案与草稿回归：85/85 通过。默认编码下组合测试另有两个旧测试使用无编码 read_text 导致 GBK 解码错误，未冒充通过。
- 对指定批次实际调用当前所选模型，验证两张 A/B 图片边界；通过正常结构闸门重建，24 单元身份、原题号、顺序和组单类型均保持，只移除前题误含的下一板块横幅。
- 保存的原答案候选原样复验，44 答案通过，使用新绑定的正式文件协议导入成功；没有重生成答案。
- 既有题型通过当前来源完整验收，正式前置组装任务 `60af1964-50ce-4d92-9440-b40552f46e5b` 完成；preassemble/types/answers 三路交接均 passed。重复 ensure 后草稿原始字节保持稳定。
- 最终聚焦回归 13/13 通过；本地续跑确定性组装 completed，model_calls=0，meta/underlines completed；validate 的 14 项全部通过，阻断失败 0、提示 0、跳过 0。自动流程按 `--stop-at review` 停在审核之前，未执行审核或远端上传。git diff --check 通过。

## 边界与回退

不修改 local_agent 的在途原始字节哈希保护；不生成缺失正文/答案，不按全局数字借答，不直接改 manifest 造完成态，不自动上传或发布。恢复旧实现仅需撤回上述两处局部代码与对应测试/文档，不覆盖其他既有改动；已验收的批次证据不能随代码回退伪造或删除。
