# 2026-11-10 修复模型选择被仓库检出覆盖，并恢复官方模型

## 背景

用户反馈：“我记得我在插件里选的模型是官方的，它显示额度已用尽。”

核查：
- 2024 卷 `answers.log` 的报错是 `TA: 429: WorkBuddy: 额度已用尽` —— 命中的是 **WorkBuddy 中转（magpie）**，不是官方模型；
- 插件设置此前把选择的模型写进仓库内 `config/model-router.json`；为了让仓库保持“默认不固定模型（default 为空）”，期间多次执行 `git checkout -- config/model-router.json`，**把用户已选的官方模型一并覆盖了**；
- 选择丢失后，路由回落到本机 Harness/兼容 profile，最终选中了已限流的 WorkBuddy 通道。

## 改了什么

1. **模型选择改为设备本地保存**：`~/.dsh/plugin-state/md-upload-tiku/model-router.json`（0700/0600）。
   - 仓库 `config/model-router.json` 只作为默认模板（`default` 为空）；
   - 首次使用时自动创建；若旧版 `config/model-router.json` 里已有可用选择，则迁移过去；
   - 升级、`git checkout`、重新克隆都不再覆盖用户选择。
2. 读取方同步：`lib/index.js`、`backend/wf-router.mjs`、`backend/wf-router.sh`、`app/workflow/local_agent.py` 统一优先读状态目录；支持 `WF_ROUTER_FILE` 覆盖（测试用）。
3. **恢复本机已选官方模型**：写入状态文件 `default = deepseek-official / deepseek-flash`（用户原选择）。仓库仍保持 `{}`。
4. Harness 兜底收敛：只在 `DSH_PROFILE` / `desktop` 里找 `agent-default-model`；该 profile 没有时使用宿主 profile 的第一个模型，不再误用 `wf-deepseek-fast` 遗留的 WorkBuddy 通道。
5. AI运维 路由优先级恢复为 **`roles.operations` → 用户设置的 default → Harness 模型**，与用户选择一致。
6. 测试：router 相关 28 项、Python 67 项通过（含状态目录与绑定用例）。

## 验证

- `node --test backend/model-list.test.cjs backend/model-router.test.cjs backend/wf-router-native.test.cjs backend/operations-settings.test.cjs`：28 项，26 通过、2 项需真实 DSH 跳过、0 失败。
- `workflow.test_route_pick + test_local_agent + test_candidate_cache + test_label_execution`：67 项通过。
- 本机状态文件已写入 `deepseek-official/deepseek-flash`；`config/model-router.json` 保持 `{}` 且工作区干净。

## 边界

- 模型选择只影响本机；不同设备可各选各的。
- 仓库默认仍为空，不会固定任何厂商/中转。
- 不改变“显式配置损坏必须报错、不静默换模型”的规则。

## 回退

恢复 `ROUTER_PATH` 指向 `config/model-router.json`，并删除迁移逻辑即可；状态文件可保留手动合并。
