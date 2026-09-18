# Docker 部署

1. 把 `.env.v2.example` 复制为 `.env`，填写 PostgreSQL 主库和日志库 DSN。
2. 将 `NEWAPI_DOCKER_NETWORK` 设置为 NewAPI 所在的 Docker 网络名；PostgreSQL 主机名应使用 Compose 服务名，例如 `postgres`。
3. 启动：

```bash
docker compose -f docker-compose.reader.yml up -d --build
```

4. 访问 `http://服务器IP:3333`。

应用容器只读取数据库，不执行迁移、不写入 NewAPI 数据库。
