# 2026-11-17：视觉移除后原生内容代理恢复路由身份绑定

## 背景/触发

批次 `【木牍教育】19年安徽中考英语-2` 的知识点阶段失败：

```
Native agent requires selected provider/model route identity
```

视觉理解链路删除时，`local_agent.invoke` 的原生内容代理分支不再注入
`_content_route_identity`；`content_agent.invoke` 严格拒绝没有 provider/model 身份的任务，
导致 knowledge/answers 等原生内容阶段无法启动。

## 改了什么

- `app/workflow/local_agent.py`：新增 `_selected_content_route()`，从当前 Harness
  `agent-default-model` 读取选中的 provider/model；原生内容代理分支在缺失
  `_content_route_identity` 时注入该身份，仍严格使用当前选中模型，不做静默换模型。
- 知识点任务重跑：19-2 `knowledge` 完成，候选已写入 `04_knowledge/knowledge.json`。

## 没改什么 / 边界

- 不固定模型、不把 provider/model 写死到仓库；仍跟随设备 Harness 选择。
- 不改变内容校验、审核差异或上传边界；`answers`/`explanations` 的原生路由同样受益。

## 如何验证

- `workflow.test_content_agent`（原生代理路由/任务测试）保持通过；
- 真实批次 `【木牍教育】19年安徽中考英语-2`：`knowledge` completed，产物 `04_knowledge/knowledge.json`。

## 回退方式

删除 `_selected_content_route()` 注入逻辑即可恢复旧行为（原生内容阶段会因缺路由身份失败）。
