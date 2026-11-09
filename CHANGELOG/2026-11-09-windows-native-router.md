# 2026-11-09 Windows 原生模型路由

## 背景

批次【木牍教育】20年安徽中考英语已修复控制台编码卡点并完成 extract、structure_gate、preassemble，但 agent 阶段在 Windows 停在 answers：原配置固定调用 `/bin/sh`，本机没有 POSIX `sh`，而 Harness 的 dsh 入口是 `dsh.cmd`。

## 改了什么

- 新增 `backend/wf-router.mjs`：保留 `model-router.json` 的选择优先级（review 轮次、角色、阶段、默认），读取现有 credentials，按路由生成/校验临时 profile，并在 Windows 通过 `cmd.exe` 调用 `dsh.cmd`；响应仍写入 `response_file`。
- `backend/local-agent.plugin.json` 的 command 改为 `node __PLUGIN_BACKEND__/wf-router.mjs {prompt_file} {response_file}`，并声明 `input_mode: none`，不再依赖 `/bin/sh` 或 PATH 上的 `dsh`。
- 新增 `backend/wf-router-native.test.cjs`，覆盖 Windows `.cmd` 入口、带空格安装路径、临时 profile 的 provider/model 写入、响应文件契约及配置不依赖 `/bin/sh`。
- Node router 通过 Harness 的 `headless` profile 加载临时路由 patch，并以 `-` 从 stdin 提交一次任务，避免误启动 Web profile；不依赖继承的 `LOCALAPPDATA`，可从 Windows 用户目录推导 Harness 安装路径。
- 生成或修复 `agent-default-model` 时删除继承的 `reasoningEffort` 并写入 `null`，避免 headless 默认 `medium` 误传给不支持推理档位的 `deepseek-official/deepseek-flash`；不改变 provider/model 选择，也不静默换模型。

## 没改什么 / 边界

- 保留原 `backend/wf-router.sh`，不改变 POSIX 环境的既有入口；本次只是给插件的 Windows agent 配置提供原生 Node 入口。
- 不修改模型选择优先级，不使用静默模型兜底；路由配置、profile、凭证或 dsh 调用失败都会返回错误。
- 不自动上传或发布；当前批次必须在真实 agent 调用成功并通过各阶段校验后继续。
- 工作区另有未提交的 `app/workflow/local_agent.py`、运维相关实现与测试、`lib/index.js` 及其他变更记录，本次保留这些并行改动，不混入 Node router 提交。

## 路由启动修正

后续核对真实 attempts 后发现，早期缺可执行文件与 Windows argv 转义问题之外，旧 router 还会把桌面 profile 整份复制到 headless profile，携带 `package.json`、bundles 及 UI 启动配置。新实现只从 base/headless patch 中保留基础包自带的 `llm-pi-ai`、`llm-deepseek`、`llm-deepseek-account` provider 配置段（包含自定义 API providers、endpoint、凭据环境变量和模型目录），过滤桌面 UI 配置；再生成独立的 `agent-default-model` overlay。overlay 内容哈希包含 provider 配置全文，变化时生成新 profile。Windows `.cmd` 参数仍以独立 argv 传递；空凭据不覆盖继承的环境变量。

## 如何验证

- `node --check backend/wf-router.mjs`：通过。
- `node --test backend/wf-router-native.test.cjs`：最终复验 6 项，5 项通过、1 项跳过（未设置 `WF_ROUTER_REAL_DSH`，条件真实 CLI 空任务测试跳过）；含 custom-provider 保留/UI 配置过滤/endpoint 变更缓存失效、Windows 双空格路径与完整 stdin argv 断言，以及 reasoningEffort 清理回归。
- 直接调用已安装 `dsh.cmd` 的 headless 真实探针：退出码 0，响应文件为 `OK`；随后批次 `answers-bag8d6rt` 正式 attempt `exit_code=0/status=completed`，生成含 81 题答案的 `response.json`。`types-f7068e7efb314a2286405af99f3e37e3` 是成功的自愈调用，不当作正式 types attempt；11:02 manifest 已确认 answers/types 均 completed。
- 真实 CLI 启动校验确认生成 overlay 可解析，但不是模型请求，也不能证明目标批次阶段已恢复；需由受控 workflow attempt 的真实产物复验。
- 全 backend suite：155 项中 138 通过、17 失败。失败分别涉及既有 lifecycle/Windows Python (`python3` 不可用，8 项)、旧 shell router 在 Windows 不可执行（5 项）、operations HTTP Python 启动失败（2 项）、review-layout 旧 CSS 断言（2 项）；定向 router 测试与这些失败无依赖。
- 批次之后因交付清单检测到产物指纹变化而重建 `preassemble`，于 11:13 失败：`Batch fill正文 conflict for group-6-7; existing content preserved`。这是独立的既有内容冲突；本次没有覆盖原内容，也未自动上传或发布。编码修复和 Windows agent 调用已验证，但整批流程仍未完成。
- `git diff --check`：最终验证通过。

## 回退方式

恢复 `backend/local-agent.plugin.json` 的 command 为原 `/bin/sh` + `wf-router.sh`，并移除 `wf-router.mjs` 与其测试即可；这会恢复当前 Windows 上 `/bin/sh` 不可执行的旧卡点，不建议在本机回退。
