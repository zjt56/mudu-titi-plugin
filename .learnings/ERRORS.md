# 错误与修复记录

## [ERR-20261109-ACL] Windows OpenSSH 私钥与凭据 ACL

**Priority**: high
**Status**: resolved
**Area**: config

### 摘要
用户指定私钥被 OpenSSH 拒绝；宿主凭据文件虽使用 POSIX 0600 创建，仍继承其他用户组读取权限。

### 错误信息
```
WARNING: UNPROTECTED PRIVATE KEY FILE!
bad permissions
Permission denied (publickey).
```

### 建议修复与验证
仅在目标私钥及仓库外凭据目录/文件上禁用宽泛继承，保留当前 Windows 用户权限。SSH 认证成功，插件配置回读成功，密码文件 ACL 仅一条当前用户许可。不记录实际凭据、服务器或私钥位置。

另：Windows PowerShell 的 Invoke-RestMethod 字符串请求体可能把中文 JSON 值转成问号。明确用 UTF-8 字节数组发送，并逐字段比较回读值，已验证路径精确一致。

### 元数据
- Reproducible: yes
- Source: error
