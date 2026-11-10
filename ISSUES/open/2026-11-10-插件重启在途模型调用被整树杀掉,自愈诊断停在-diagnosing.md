# 卡点：插件重启在途模型调用被整树杀掉，自愈诊断停在 diagnosing

- id: 2026-11-10-插件重启在途模型调用被整树杀掉,自愈诊断停在-diagnosing
- status: open
- batch: 【木牍教育】23年安徽中考英语
- batch_dir: workflows/【木牍教育】23年安徽中考英语
- stage: explanations
- check: backend_restart_mid_flight
- created: 2026-11-10T16:21:11+08:00
- last_seen: 2026-11-10T16:21:11+08:00
- occurrences: 1
- title: 插件重启在途模型调用被整树杀掉，自愈诊断停在 diagnosing

## 现象（卡点）

15:56/15:59/16:02 三次自愈事件 outcome=diagnosing、无 finished_at，self-heal-runs/*/stderr.log 只有 wf-router 选模型一行；同一秒插件重新 apply、python 后端 PID 换新

## 具体检查与判定

- 失败检查：backend_restart_mid_flight
- 判定口径：后端回收用 taskkill /T /F 杀整棵进程树，模型调用（node wf-router -> dsh CLI）随之消失；插件侧 800ms 健康探测失败即判死并 retire，加上 .py 热重启不做在途判断
- 修复方向：后端 /api/health 暴露 busy；插件重启前检查 busy 并延后到空闲（上限 30 分钟），健康探测失败再给两次 4 秒窗口

## 修复记录

（未修复）
