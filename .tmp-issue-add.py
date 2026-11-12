# -*- coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Administrator\.dsh\plugin-src\md-upload-tiku")
sys.path.insert(0, str(ROOT / "app"))
from workflow import issue_log  # noqa: E402

BATCH = "05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec"
BATCH_DIR = r"C:\Users\Administrator\.dsh\wf-plugin-data\workflows\05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec"

queue = issue_log.add(
    BATCH,
    "queue",
    "排队批次静默停摆：工作台后端退出后无人自动拉起",
    check="WORKBENCH_BACKEND_ABSENT",
    symptom=(
        "2026-11-12T16:52:04 validate 完成后，批次目录直到 17:13:08 零写入（21 分钟）；"
        "17:11 进程快照中不存在 python -m workbench.server，17:13:09 页面请求触发 ensureBackend 后后端才启动，"
        "2 秒内即派发 pending 的 review。"
    ),
    why=(
        "批次推进队列（server.py _advance_worker_loop）与 WorkflowSupervisor 都只活在 workbench.server 进程内；"
        "lib/index.js 的 spawnBackend 在子进程 exit 时只清 backend.proc/instanceId/ready，不做任何重启，"
        "ensureBackend 也只在宿主页面/接口请求时才调用。因此后端一旦退出（插件重载、热重启失败、崩溃），"
        "队列没有任何自愈路径，界面持续显示排队中但不推进。"
    ),
    how=(
        "lib/index.js 增加后端存活看门狗：每 15 秒检查 backendChildAlive(backend.proc)，不在就 ensureBackend() 自动拉起，"
        "连续失败按 15/30/60 秒退避；插件激活时 startBackendWatchdog()，停用时 stopBackendWatchdog()。"
    ),
    batch_dir=BATCH_DIR,
)
print("QUEUE", queue)

fmt = issue_log.add(
    BATCH,
    "format_gate",
    "格式闸门确定性失败：表格单元格内图片平台契约无法表达",
    check="LAYOUT_CELL_MEDIA_UNSUPPORTED",
    symptom=(
        "07_review/upload-format.json: status=failed passed=false prepare_upload_required=true，"
        "errors 3 条全为 LAYOUT_CELL_MEDIA_UNSUPPORTED（unit_id=sec-242483090077723c-u121，rows 0/1/2，column 0），"
        "message「平台附件只支持题干/选项/答案/解析位置，不能表达图片所属表格单元格」。"
    ),
    why=(
        "u121 的 [TABLE 4] 三行首格原文为 [IMG image18.jpeg]<br>Alice 等单元格内图片；"
        "远端 materialBlocks 契约（http://101.43.1.57/docs/）单元格仅支持 text/items/rowspan/colspan/header，"
        "attachments 只能绑定题目/选项/答案/解析，无法表达单元格级图片，故 source_objects.capabilities 产出该能力错误并阻断。"
    ),
    how=(
        "待用户确定口径：(a) 保持阻断等平台扩展单元格媒体协议；"
        "(b) 图片按单元级 placement=question 交付、单元格文本保持忠实，把该限制降为带代码的显式 warning，"
        "本批即可续跑到 upload_gate；不得静默丢图或伪造成功。"
    ),
    batch_dir=BATCH_DIR,
)
print("FORMAT", fmt)
