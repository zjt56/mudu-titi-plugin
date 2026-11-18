# 卡点：上传暂存人工会话登录被服务端拒绝（invalid_credentials）

- id: 2026-11-18-上传暂存人工会话登录被服务端拒绝(invalid_credentials)
- status: open
- batch: 【木牍教育】20年安徽中考英语
- batch_dir: workflows/【木牍教育】20年安徽中考英语
- stage: upload
- check: login_failed
- created: 2026-11-18T13:17:25+08:00
- last_seen: 2026-11-18T13:17:25+08:00
- occurrences: 1
- title: 上传暂存人工会话登录被服务端拒绝（invalid_credentials）

## 现象（卡点）

上传暂存未完成：Upload API request failed (login_failed); HTTP 401；07_upload/attempts/9e94a136….json state=rejected error_code=login_failed http_status=401；直接复现 POST /api/auth/login（账号 001 + 插件已存密码）返回 401 {"error":"invalid_credentials"}

## 具体检查与判定

- 失败检查：login_failed
- 判定口径：提交暂存前必须用人工账号建立会话（API Key 被服务端视为 harness，不能创建暂存）。同一个已存密码（文件 2026-11-06 11:43 后未改动、ASCII 19 字符、无空白/BOM）在 2026-11-18 09:58 成功暂存过 17 年批次，13:12 同一账号密码却被 /api/auth/login 判 invalid_credentials；隧道 /api/health 200、目录读取（API Key）正常，且响应体不是限流/CAPTCHA/账号锁定，故排除网络、API Key 与本地客户端，判定为服务端账号或密码在两次上传之间发生变化/被停用。
- 修复方向：在插件「上传设置」重新填写题库人工账号与密码并保存（写 login-password），再重试上传；若确认账号密码无误，需在题库服务器核对人工账号 001 是否存在/被停用/被重置，必要时由管理员重置密码。凭据修复前不得改用 API Key 绕过人工会话，也不得重复空跑登录。

## 修复记录

（未修复）
