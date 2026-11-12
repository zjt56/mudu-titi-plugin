# 2026-11-12 独立 pi-ai 原生图片调用桥

## 背景

现有视觉路径硬编码 magpie，不能跟随插件所选模型。用户要求保留 Word 原生精确文字提取，另用真实图片输入理解视觉结构；禁止静默换 provider/model，禁止让纯文字 CLI 猜自己已看图。

## 改了什么

- 新增 `backend/wf-vision.mjs`：JSON 输入/输出，严格读取设备插件模型路由与 Harness 所选默认模型。沿用角色/阶段/review round 优先级；显式损坏配置及未知/不支持图片的模型失败，无 bootstrap fallback。
- 直接使用当前已安装 Harness pi-ai provider adapter；桌面发行版通过配套 Electron Node 原生只读加载 ASAR。模型、端点、协议取自当前 profile/catalog，无裸猜 URL。多图一次调用，真实 base64 图片块带独立 ID；原生 SDK sessionId 满足 OpenCode Go 的会话头契约。
- 新增 `app/workflow/visual_transport.py`：`identify(images,prompt,stage,max_tokens,timeout)` 与无模型调用的 `route_identity(stage)`，供上层按真实路由失效缓存。
- 新增桥接口文档及 Node/Python 回归测试。凭据仅内存读取，兼容新版 `.credentials.yaml.refs` 与旧布局，不向输出泄露密钥或原始 SDK 日志。
- 随后依主 Agent 扩展委托，将 `visual_review.build` 默认接入新桥：仅正式 `01_source/media`、字节去重且保留原path、最多8图一次调用，hash+route+prompt缓存，首错停止后续调用。复用同路由且通过原件/正文/媒体绑定验证的 lesson_bands 横幅证据。显式endpoint参数/环境保留旧接口兼容。
- 切路由失败会将旧活动图片语义按内容哈希保全至 `visual-semantics-history`，活动items置空，防止旧证据被误消费。失败响应不会成为识别缓存。route_identity 与 identify 统一补入 `Agent: <stage>`，避免角色覆盖与阶段缓存身份不一致。

## 边界

未修改 `wf-router.mjs` 的文字协议，未修改 Harness 实现/凭据/用户路由；未修改 lesson_bands、extract、runner。visual_review 的默认传输改动由主 Agent 后续明确委托；旧显式endpoint兼容测试同步限定入口。未提交 Git。调用者仍负责模型文字结果的 JSON/schema 校验及证据缓存；模型能力不等于工作流质量通过。未知媒体格式明确失败，不用 OCR 偷换。

## 实际验证

- `node --test backend/*.test.cjs`：266项，264通过、2跳过、0失败（视觉桥6项）。
- `PYTHONPATH=app python3 -m unittest workflow.test_visual_transport`：6项通过。
- 后续局部复验：`workflow.test_visual_review workflow.test_visual_transport workflow.test_visual_crosscheck` 共24项通过；`node --test backend/wf-vision.test.cjs` 共7项通过（新增Windows打包布局模拟）。完整backend suite早于最终局部边界修复执行，最终全量由主Agent统一集成运行。
- 一次完整workflow suite刚启动即因主Agent正在并行集成而主动取消，不作为全量通过；禁止因此声称完整workflow通过。
- 当前用户插件所选 `opencode-go/deepseek-v4.1-flash`，桌面 Harness pi-ai 1.3.0，真实Word两张内嵌图片单次调用成功：`banner-a` → `band` / `A 基础巩固`；`image-b` → `other` / `Aa Cc Ee Gg Hh Ii Kk Mm Oo Rr Tt Vv Xx Zz`。stop_reason=stop；input=513，output=925（含reasoning861）。结构化结果保存在临时证据文件 `/tmp/wf-vision-live-result.json`，无凭据。
- 诊断发现旧 npm pi-ai 0.82.1 缺少 v4.1 模型，改用当前已安装桌面发行版；OpenCode Go 首次返回 MissingSessionID 后按 SDK sessionId 选项补齐，复验成功。没有替换模型。

## 回退

新模块独立；停止上层调用并移除上述新增文件即可，不涉及旧文字路由或用户数据迁移。
