# 2026-11-09 控制台编码加固：阶段不再卡死在 running

## 背景

Windows 上 Python 的 stdout 走 cp936(GBK) 管道。`extract` 明明已经把产物全部落盘，父进程回显日志那一行却抛 `UnicodeEncodeError`（日志里含 U+FFFD 或 GBK 编不出的字符），执行器直接崩掉，阶段永远停在 `running`；监督器把「仍在 running」当成未完成，每 30 秒重派一次，整卷（含 PDF OCR）被反复重跑。

2020 安徽卷的 `extract` 卡死即因此而起：不是提取失败，是回显失败。

## 改了什么

- `app/workflow/runner.py`
  - 新增 `_harden_stdio()`：把本进程 stdout/stderr 改成 `errors="replace"`，任何字符都不再让流水线崩；`_harden_stdio()` 在导入时执行一次，并在每次执行阶段前重设（worker 线程/捕获 stdout 的宿主可能替换过流）。
  - 新增 `_stage_env()`：阶段子进程环境注入 `PYTHONIOENCODING=utf-8`，父进程按 UTF-8 解码，中文日志不再是 U+FFFD；返回副本，不改父进程 `os.environ`。
  - 新增 `_echo()`：回显单行；`UnicodeEncodeError`/`ValueError` 一律降级为 ASCII 替换文本或静默，绝不让回显中断阶段。
  - 新增 `_mark_crashed()`：执行器自身抛异常时，把该阶段从 `running` 标成 `failed`（exit_code=1，notes 记异常前 300 字）；**只动 `running`**，已到终态的判定不被覆盖，未开始的仍是 pending；经 `local_agent._agent_lock(30)` 重读 manifest 后写入。
  - `_execute_command()` 拆成薄包装 + `_execute_command_stage()`：包装层捕获异常 → `_mark_crashed()` → 原样 `raise`，异常仍上抛，但阶段不再看起来「活着」。
- `lib/index.js`：`spawnBackend()` 给后端子进程注入 `PYTHONIOENCODING: 'utf-8'`，与阶段子进程口径一致。
- 新增 `app/workflow/test_stage_console_encoding.py`（7 个用例）。

## 没改什么 / 边界

- 不改阶段契约、不改产物形状、不改任何校验口径：失败的阶段依然是 `failed`，不会因为「能回显」就变成通过。
- 不吞执行器异常：`_mark_crashed` 之后仍原样抛出，调用方照旧看到失败。
- 只处理编码与「卡在 running」两类问题，不涉及提取质量、答案/解析正确性。

## 如何验证

- `cd app && PYTHONPATH=. python -m unittest workflow.test_stage_console_encoding -v` → **7 个用例全通过**（`Ran 7 tests ... OK`）：
  - `test_echo_survives_strict_gbk_console`：GBK 严格流上回显中文不抛；
  - `test_echo_swallows_closed_stream`：流已关闭时回显静默；
  - `test_stage_env_makes_child_print_utf8`：子进程实际按 UTF-8 输出（中文不再变 U+FFFD）；
  - `test_stage_env_does_not_mutate_parent_env`：不改父进程环境；
  - `test_command_output_reaches_log_as_utf8`：阶段日志以 UTF-8 落盘；
  - `test_real_child_output_lands_in_log_without_replacement`：真子进程 `print` 的中文原样进日志、日志内 U+FFFD 计数为 0（这是端到端补证，不是打桩）；
  - `test_executor_crash_leaves_failed_not_running`：执行器崩溃后阶段为 `failed` 而非 `running`。
- 回归对照：`workflow.test_runner_auto` 等 10 个受影响模块（163 用例）在「改动前基线（同树副本 + 中和 `_stage_env`/`_echo`）」与本改动后失败集合**完全相同**（2 failures / 19 errors，均为本机 cp936 locale 下 `read_text()`/`write_text()` 未指定编码的既有问题），即本次改动未引入新失败；本改动后新增的 7 个用例全绿。
- 实批验证：`【木牍教育】20年安徽中考英语`↔`C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】20年安徽中考英语` 在改动生效后从 `extract` 一路推进到 `preassemble`（`extract` 09:45:56→09:46:00 exit 0、`structure_gate` 09:46:01 exit 0、`preassemble` 09:46:01→09:46:02 exit 0），不再卡在首阶段、也不再每 30 秒重跑整卷 OCR。
- 顺带暴露的下一个卡点（**不是本次改动引入、也未被本次改动掩盖**）：该批次推进到 `answers`（agent 阶段）时报 `Agent executable not found or not executable: /bin/sh`（`workflow/supervisor-result.json`）。原因是 `backend/local-agent.plugin.json` 把 `command[0]` 写死成 `/bin/sh`，而本机 PATH 上没有 `sh`（只有 Git Bash 的 `sh.exe` 与 harness 自带 `resources/runtime/cli/bin/dsh.cmd`，二者都未接线），`local_agent._validate_config` 因此直接判配置错误。这是 Windows 端的 agent 桥接线问题，需单独处理（见下节）。

## 回退方式

单次改动集中在 `runner.py` 与 `lib/index.js` 两处，`git revert` 对应提交即可；回退后将恢复「GBK 回显即崩、阶段停在 running」的旧行为，不建议。

## 未决点（需另开一项处理，不在本次范围）

Windows 上 agent 阶段（answers/types/explanations/knowledge/assemble/review）全部依赖 `backend/wf-router.sh`，而配置把 `command[0]` 写死为 `/bin/sh`、脚本内部默认用 PATH 上的 `dsh`。本机两者都不成立：

- PATH 无 `sh`（`Get-Command sh` 为空）；Git Bash 的 `C:\Program Files\Git\usr\bin\sh.exe` 存在但未接线。
- PATH 无 `dsh`；harness 自带 `resources/runtime/cli/bin/dsh.cmd`（`.cmd` 需经 `cmd.exe` 才能从 POSIX shell 调用，MSYS bash 不能直接 exec）。

要让批次在这台 Windows 机器上继续，需要把路由适配器做成 Windows 原生入口（Node 或 Python 版 router，直接以 `dsh.cmd`/`node dsh.js` 调用），而不是让 Python 工作流去 `exec /bin/sh`。这次只修了编码导致的「卡在 running」，没有改 agent 桥的这一层接线；在接线完成前，批次会停在 `answers`，错误即上文 `/bin/sh`。
