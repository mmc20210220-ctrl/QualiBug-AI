# 部署说明

## 本地 Docker Compose

```bash
docker compose up -d --build
```

## 重置数据

```bash
./scripts/reset-db.sh
```

## 健康检查

```bash
./scripts/health-check.sh
```

## 常见问题

### 端口冲突

默认端口：

- 3001 用户端；
- 3002 管理端；
- 8080 API Gateway；
- 55432 PostgreSQL；
- 8001-8010 内部服务。

如果端口冲突，可以改 `docker-compose.yml` 左侧映射端口。
