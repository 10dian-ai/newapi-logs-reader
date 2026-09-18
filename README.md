# NewAPI Logs Reader

NewAPI 专用只读日志浏览器：连接 NewAPI 主库与日志库，使用 Root 用户验证后，在 `3333` 端口检索调用日志。

## 功能

- PostgreSQL 主库与日志库双连接
- Root 用户登录验证（主库 `users` 表）
- prompt、response、模型、用户、状态和 NewAPI 元数据搜索
- 普通关键词、正则表达式和筛选条件组合搜索
- 详情默认脱敏，用户可在详情面板手动展开原始值
- 手动刷新与实时刷新
- JSON / CSV 导出
- 只读查询，不迁移或写入 NewAPI 数据库

## Docker 部署

1. 复制环境变量模板：

   ```bash
   cp .env.v2.example .env
   ```

2. 编辑 `.env`，填写主库和日志库 DSN，以及随机的 `SESSION_SECRET`。不要把 `.env` 提交到 Git。

3. 将 `NEWAPI_DOCKER_NETWORK` 设置为 NewAPI 所在的 Docker 网络，然后启动：

   ```bash
   docker compose -f docker-compose.reader.v2.yml up -d --build
   ```

4. 打开 `http://服务器IP:3333`，使用 NewAPI Root 用户名和密码登录。

详细说明见 [DEPLOY_DOCKER.md](DEPLOY_DOCKER.md) 和 [RUNNING_V2.md](RUNNING_V2.md)。

## 本地运行

```powershell
Copy-Item .env.v2.example .env
.\\start_reader.ps1
```

应用入口是 `server_entry.py`，它兼容 NewAPI 官方的 `SQL_DSN` 和 `LOG_SQL_DSN` 环境变量，并启动 `server_v2.py`。

## 数据说明

NewAPI 默认 `logs` 表可能只包含调用元数据；如果部署没有记录完整请求和响应的扩展表，历史 prompt / response 无法从已有日志推导。程序会探测可用的 `payload_logs` 等表并展示实际存在的数据。
