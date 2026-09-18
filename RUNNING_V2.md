# NewAPI Logs Reader v2

在项目目录创建 `.env`（参考 `.env.v2.example`），确认容器网络中能解析 PostgreSQL 服务名，然后执行：

```powershell
cd 'D:\program\newapi-logs reader'
.\start_v2.ps1
```

打开 `http://127.0.0.1:3333`，使用 NewAPI 的 Root 用户名和密码登录。程序只允许 `users.role = 100` 且 `users.status = 1` 的账号进入。

主库通过 `SQL_DSN` 连接，用于 Root 登录验证；日志库通过 `LOG_SQL_DSN` 连接，用于 `logs` / `payload_logs` 查询。两个 DSN 都建议使用只读 PostgreSQL 账号，日志库不应暴露公网。
