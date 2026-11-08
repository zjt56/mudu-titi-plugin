# 初始化引导（新装插件的第一站）

插件本体很小；听力运行时与识别模型按需下载。新用户装完插件后，
「模型与设置 → 初始化引导」会把**要装什么、要下什么、要配什么**列成一页。

## 六项检查

| 项 | 是否必须 | 判定方式 | 一键动作 |
|---|---|---|---|
| Python 运行时 | 必须 | `/wf-plugin/health` 的 `python` | 随插件分发的便携版；缺失需重装插件 |
| ffmpeg / ffprobe | 必须 | `/api/audio/config` 的 `ffmpeg`/`ffprobe` | 插件 vendor 目录或系统 PATH |
| 听力模型（WhisperX + 识别模型） | 按需 | 同上 `ready` | **安装/更新**：`POST /api/audio/bootstrap`（已就绪时 `force:true` 真正重下） |
| OCR 识别 | 按需 | `/wf-plugin/ocr` 的 `has_token` | 跳转「OCR 识别」填 Token |
| 大模型 | 必须 | `/wf-plugin/config` 的 `router.default.model` | 跳转「大模型配置」填 Key 选模型 |
| 题库连接 | 按需 | `/wf-plugin/question-bank` 的 `has_api_key` | 跳转「上传设置」 |

## 设计要点

- **默认落在引导页**：设置页第一个栏目，新装用户第一眼就看到。
- **判定不靠默认值**：OCR 的 `endpoint`、题库的 `base_url` 都有默认值，
  所以只认 `has_token` / `has_api_key`，避免"看起来配好了其实没密钥"。
- **下载是一个按钮**：听力运行时+模型通过既有 bootstrap 触发，
  进度在「听力切分」页看；引导页只负责启动与复检。
- **更新走 force**：已就绪时按钮变成「重新安装 / 更新模型」，用 `force:true`
  绕过 READY 标记重新下载，适合模型升级。
- **不阻塞**：引导页只是体检与入口，不改变任何阶段的执行/审核/上传口径。

## 不在引导里做的事

- 不自动上传/发布。
- 不在安装时静默换模型（模型选择仍在「大模型配置」，配置缺失直接报错）。
- 不替用户决定要不要 OCR/听力：只显示状态，由用户按需开启。
