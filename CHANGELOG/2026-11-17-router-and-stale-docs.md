# 模型路由显式损坏配置与历史说明收敛

## 背景/触发

架构审计发现，`roles`、`stages`、`review` 中存在但不完整的模型条目会被当成“没有配置”并静默回退到更低优先级或 Harness 默认模型；宿主 `readRouter()` 也会把损坏的本机 JSON 当成空路由。审计同时发现根规则、独立审核兼容脚本和测试说明仍保留视觉理解及“三审”旧表述。

## 改了什么

- `backend/route_pick.py`、`backend/wf-router.mjs`：显式存在但缺 `provider/model` 的 default、role、stage、review round 现在直接报错；只有不存在的条目才按既定优先级继续查找。默认 `{}` 仍表示跟随设备 Harness 模型。
- `lib/index.js`、`lib/operations-settings.js` 调用链：损坏的本机路由 JSON 不再被当作空配置；AI 运维显式配置不完整时不再回退到 Harness。
- `backend/model-router.test.cjs`、`backend/wf-router-native.test.cjs`、`backend/model-list.test.cjs`：补充损坏 default、stage、review round、operations role 的负例。
- `AGENTS.md`、`app/AGENTS.md`、`README.md`、`app/docs/extraction.md`：删除现行说明中的视觉调用和“三审”要求；测试命令覆盖 `.test.cjs` 与 `.test.mjs`。
- `package.json`：移除未接通的“自定义提示词/审核口径”能力声明；兼容 REST 读写接口暂不删除，等待外部调用核查。
- `app/workflow/independent_review.py` 与 `mudu-flow-review.workflow.js`：明确这是 `MUDU_REVIEW_CONFIG` 显式启用的独立兼容闸门，统一使用“独立抽样复核”措辞，不改变主流程两轮审核契约。

## 没改什么 / 边界

- 没有改写模型选择、用户设备配置、用户批次或上传边界；仓库模板 `config/model-router.json` 仍保持空 default。
- 没有删除 `backend/wf-vision.*` 桥接文件，避免破坏未知外部脚本；它们仍列为后续确认后的清理候选。
- 没有把审核差异、能力受限或独立验签改成自动通过。

## 如何验证

待本次修改后的定向 Node 回归执行后补充实际通过数字；至少覆盖：

- `node --test backend/model-router.test.cjs backend/wf-router-native.test.cjs backend/model-list.test.cjs`：32 项，30 通过、2 跳过、0 失败。
- `node --check lib/index.js lib/client.js`：通过。
- `python3 -m py_compile backend/route_pick.py app/workflow/independent_review.py`：通过。

## 回退方式

回退本提交涉及的路由和文档文件即可；不触碰本机 `~/.dsh/plugin-state/md-upload-tiku/model-router.json` 与用户批次数据。
