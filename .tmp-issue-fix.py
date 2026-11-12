# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
from workflow import issue_log  # noqa: E402

r1 = issue_log.fix(
    "2026-11-12-排队批次静默停摆-工作台后端退出后无人自动拉起",
    resolution=(
        "衔接根因：批次推进队列与 WorkflowSupervisor 只活在工作台后端进程内，而 lib/index.js 在子进程 exit 时只清 "
        "backend.proc/instanceId/ready/lastError，没有任何重启路径，ensureBackend() 仅在宿主请求时调用，"
        "故后端一退出队列就永久停摆。修复：lib/index.js 新增后端存活看门狗（backendWatchdogTick/startBackendWatchdog/"
        "stopBackendWatchdog，15s 轮询，失败按 15s→30s→60s 退避，上限 60s），插件激活时用 ctx.effect 启停；"
        "检测到子进程不在就 await ensureBackend() 自动拉起，健康后端不动、热重启期间不抢启动。"
        "backend/backend-lifecycle.test.cjs 补 3 个用例（退出后拉起 / 健康不重复 spawn / 调度器幂等可停）。"
    ),
    commit="0bd03a7",
    verify=(
        "node --check lib/index.js lib/client.js 通过；node --test backend/backend-lifecycle.test.cjs 16 项 8 通过，"
        "其中新增 3 个看门狗用例全部通过，剩余 8 个失败项在未改动 HEAD 基线上同样失败（含本机无 python3 的真实后端用例），"
        "非本次回归。真实观察：17:13:09 后端被 ensureBackend 拉起后 2 秒内即派发 pending 的 review，"
        "3 个分片 17:13–17:22 全部完成，证明后端活着时队列可正常推进。"
    ),
)
print("fix1:", r1)

r2 = issue_log.fix(
    "2026-11-12-格式闸门确定性失败-表格单元格内图片平台契约无法表达",
    resolution=(
        "按用户明确选择（降为显式 warning、继续跑到 upload_gate）：app/workflow/source_objects.py 新增 "
        "NON_BLOCKING_CAPABILITY_CODES={'LAYOUT_CELL_MEDIA_UNSUPPORTED'} 与 layout_issues() "
        "（upload_errors() 退化为只返回阻断项的薄封装），单元格内图片进 warnings 并标 non_blocking=True、"
        "消息说明图片已按单元级附件随题交付、表格文字保持原文；嵌套表格、来源绑定不成立、投影不一致仍进 errors。"
        "upload_payload.build_payload 同时并入 errors 与 warnings。"
    ),
    commit="0bd03a7",
    verify=(
        "workflow.test_source_objects 18 项 OK（含 test_cell_media_limitation_is_visible_warning_not_a_gate_failure、"
        "test_nested_table_capability_still_blocks_the_final_gate）；workflow.test_upload_payload + "
        "workflow.test_format_gate 95 项 OK。真实批次 05七上...StarterUnit2 复跑 format_gate："
        "17:38:26 completed，07_review/upload-format.json status=passed、errors=[]，3 条 "
        "LAYOUT_CELL_MEDIA_UNSUPPORTED 落在 warnings(non_blocking=true)，manifest 状态 pending_confirmation，"
        "停在 upload_gate（人工，不自动上传）。"
    ),
)
print("fix2:", r2)
