# 运行说明

在 PowerShell 中执行：

```powershell
cd 'D:\program\newapi-logs reader'
Set-ExecutionPolicy -Scope Process Bypass
.\start.ps1
```

浏览器打开 `http://127.0.0.1:3333`。

连接真实数据库前设置：

```powershell
$env:DB_DSN = 'sqlite:///D:/path/to/one-api.db'
# 或 PostgreSQL / MySQL DSN
$env:LOG_DB_DSN = ''
```

建议使用 NewAPI 的只读数据库账号。`LOG_DB_DSN` 存在时，日志查询优先使用独立日志库。
