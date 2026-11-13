# 原生会话清理失败后允许重新删除

## 背景
指定批次删除时，旧宿主缺少 `sessions` 注入导致原生清理失败。即使宿主注入已经修复，后端仍会复用失败回执并立即报错，无法再次提交清理。

## 修改与边界
`app/workflow/content_agent.py` 在下一次明确删除时替换失败票据，生成新的标识并要求宿主重新检查、清理和确认；待处理票据仍被复用，失败不会自动变为成功。运行锁、会话存活、身份与路径检查保持不变。

## 验证
`PYTHONPATH=app python3 -m unittest workflow.test_content_agent workflow.test_workbench_delete_batch`：53 项通过；`node --test backend/host-inject-declarations.test.cjs backend/content-agent.test.cjs`：51 项通过。真实指定批次通过工作台删除接口返回 `purged=true`，列表、批次目录、收件箱、常用归档目录及内容智能体队列未发现该批次。

## 回退
撤销本次 broker 和对应测试改动即可恢复旧回执行为；已删除批次无法恢复。
