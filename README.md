# NewAPI Logs Reader

面向 NewAPI 的 PostgreSQL 日志浏览器，默认运行在 3333 端口。

## 功能

- 主库 + 独立日志库双连接：SQL_DSN / LOG_SQL_DSN
- Root 用户登录验证（支持 NewAPI Argon2id 和历史 bcrypt）
- 日志分页、模型/用户/状态筛选、正则搜索
- 10 秒实时刷新
- CSV / JSON 导出
- 详情抽屉与敏感字段默认脱敏，支持手动展开
- 自动探测 logs、payload_logs 表和字段
- Docker Compose 部署

## 启动

`powershell
Copy-Item .env.v2.example .env
# 编辑 .env，填写 PostgreSQL 主库和日志库 DSN
.\start_reader.ps1
`

打开 http://127.0.0.1:3333，使用 NewAPI Root 用户名和密码登录。

Docker：

`ash
docker compose -f docker-compose.reader.v2.yml up -d --build
`

NewAPI 默认 logs 表保存调用和计费元数据，通常不包含历史 prompt/response。只有数据库中存在 payload_logs 或其他采集表时，详情页才能展示历史正文。
