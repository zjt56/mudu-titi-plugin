# 独立视觉调用桥

`wf-vision.mjs` 与 `workflow.visual_transport` 只负责图片传输，不改变提取、审核或工作流状态。

## 协议

```sh
node backend/wf-vision.mjs < request.json
# 或 node backend/wf-vision.mjs request.json response.json
```

输入：

```json
{"prompt":"Stage: extract\n只输出每张图片的结构化分类","images":[{"path":"/absolute/banner.jpg","id":"banner"}],"max_tokens":1200}
```

成功输出包括 `provider`、`model`、`text`、`stop_reason`、`usage`、`images`（ID/MIME/字节数）、`transport=pi-ai` 和 `adapter_version`。`text` 是模型原始文字，调用者负责 JSON/schema 校验。失败输出 `{ "error": { "code": "…", "message": "…" } }` 并以非零状态退出；不改用 OCR 或其他模型。

Python：

```python
from workflow.visual_transport import identify, route_identity, VisualTransportError
route = route_identity(stage="extract")
result = identify([{"id": "banner", "path": "/absolute/banner.jpg"}],
                  '只输出 JSON', stage="extract", max_tokens=1200, timeout=90)
```

`route_identity` 只读返回所选 provider/model/profile，不调用模型、不读取凭据。缓存应绑定它的结果。两者都会默认插入 `Agent: <stage>`，保证角色覆盖优先级一致；identify的prompt已有明确Agent时保留。`identify` 将所有图片放入**同一条用户消息、同一次调用**，使用 MIME + base64 图片块和独立 ID 标签，绝不要求文字 CLI 自述是否看图。

## 路由与运行时

- 与 `wf-router` 相同：`WF_ROUTER_FILE` → 设备 plugin-state 路由 → 仓库模板；角色覆盖优先于阶段，review rounds 优先。默认空时只跟随明确的 Harness `agent-default-model`；不从第一个可用模型引导兜底。
- 显式 provider/model 缺失、配置损坏、未知模型、无图片能力、无凭据、模型失败/截断均明确失败。不会修改用户路由、profile 或凭据文件。
- 优先发现 DSH launcher 配套桌面应用，使用 `ELECTRON_RUN_AS_NODE=1` 原生加载只读 app.asar 中的 pi-ai；再支持 unpacked/npm 的 pi-ai。`WF_PI_AI_ROOT` 可显式指定已安装包，`WF_VISION_NODE` 可指定 Node。包发现不允许替换 provider/model。
- 所选 provider 的模型/endpoint/protocol 从当前 Harness profile 与 pi-ai 内置 catalog 精确解析；没有权威 endpoint/protocol 则拒绝猜测。内置 provider 继续拥有其协议兼容与认证行为。
- `apiKeyEnv` 先读环境，再读 `.credentials.yaml.refs`（兼容旧平面布局）。密钥只在内存传给 SDK；结果和异常不输出凭据/原始 SDK 日志。SDK 调用带独立 sessionId，由 OpenCode provider 生成其原生会话头。
- 支持 PNG/JPEG/GIF/WebP；按字节签名识别格式，拒绝不支持的媒体，调用方先转换 WMF/EMF 等 Word 原生媒体。

## 验证

2026-11-12 当前设备插件选中 `opencode-go/deepseek-v4.1-flash`。实际调用桌面 Harness pi-ai 1.3.0，一次发送真实 Word 的两张嵌图：返回 `banner-a` 为 `band`、文字 `A 基础巩固`；第二张为 `other`、文字 `Aa Cc Ee Gg Hh Ii Kk Mm Oo Rr Tt Vv Xx Zz`。未改变任何用户配置。旧 npm 缓存 pi-ai 0.82.1 缺少该模型，不能拿它的旧服务地址推测当前模型。

回归：完整 `node --test backend/*.test.cjs` 为 264 passed / 2 skipped / 0 failed（当时含视觉桥6项）；最终局部视觉桥7项通过，Python `visual_review + visual_transport + visual_crosscheck` 24项通过。最终workflow全量由主Agent统一集成运行。

`visual_review.build` 默认使用此桥，只消费 `01_source/media` 的独立媒体，最多8张一次调用，按字节去重但保留原path。缓存绑定hash+route+prompt；同路由且原件/正文/媒体验证通过的lesson_bands横幅证据可复用。首个失败停止后续批次。已有旧活动证据在新路由失败时按哈希保全到history，再将活动items置空，防止旧结果冒充当前结果。显式endpoint参数或 `VISUAL_REVIEW_ENDPOINT` 才启用旧兼容路径，不作自动fallback。
