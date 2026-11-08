# 2026-11-08 新增「初始化引导」页（新装插件一站式体检 + 一键下载）

## 背景 / 触发

用户提出：后续给其他用户安装时，对方可能没装听力识别模型、OCR 与各项配置；
插件本体小、模型大，希望有一个引导装置，把**下载地址与配置**做成自动化/一个按钮，
或者在插件里直接提供「更新大模型」的入口。

## 改了什么

1. `lib/client.js`：设置页新增第 1 个栏目「初始化引导」（默认打开），六项体检：
   Python 运行时、ffmpeg/ffprobe、听力模型、OCR、大模型、题库连接。
   每项显示「必须/按需」+ 就绪徽章 + 说明 + 动作按钮。
2. 动作：
   - 听力模型：`POST /wf-plugin/api/audio/bootstrap`；**已就绪时传 `force:true`**
     真正重新下载（用于更新模型），未就绪时正常安装。
   - OCR / 大模型 / 题库：一键跳转到对应栏目。
   - 「重新检查」重新拉取全部状态。
3. 判定口径：OCR 用 `has_token`、题库用 `has_api_key`（宿主不回密钥本身），
   **不用 endpoint/base_url 的默认值**判断"已配好"。
4. `backend/settings-navigation.test.cjs`：更新为五项导航并断言默认落在引导页。
5. `backend/model-router.test.cjs`：并发断言从 16 改为 4（本地内存保护口径）。
6. 文档：`app/docs/onboarding.md`。

## 没改什么 / 边界

- 引导页只做体检与入口，不改变执行/审核/上传口径；不自动上传/发布。
- 不静默换模型：模型选择仍在「大模型配置」，配置缺失直接报错。
- 不替用户决定是否启用 OCR/听力，只显示状态。
- 听力安装复用既有 `audio/bootstrap.py`（venv + WhisperX + 模型），未重写。

## 如何验证

- `node --test backend/*.test.cjs backend/*.test.mjs`：**133 项通过**。
- `node --check lib/index.js lib/client.js`：通过。
- 实测宿主接口：`/wf-plugin/health`（python 有）、`/api/audio/config`
  （`ready:true`、`ffmpeg` 有路径）、`/wf-plugin/ocr`（`has_token:true`）、
  `/wf-plugin/config`（`router.default.model` 有）、`/wf-plugin/question-bank`（`has_api_key:true`）。

## 生效范围

插件前端改动需**重载插件/重启宿主**后生效。

## 回退

删除 `OnboardingSection` 与 sections 中的 `onboarding` 项即可。
