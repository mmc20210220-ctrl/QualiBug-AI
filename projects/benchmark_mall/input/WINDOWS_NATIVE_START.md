# Windows 本机非 Docker 启动说明

适用场景：Docker Desktop 无法安装或无法运行，但本机已经安装 PostgreSQL、Node.js。

## 一、运行单元

本靶场项目是独立企业系统，实际包含：

- PostgreSQL 数据库：1 个
- 后端 Node 服务：11 个
- 前端 Vite 服务：2 个

但在 QualiBug 产品中只需要维护：企业资料、前端地址、API 地址、数据库账号、测试账号。

## 二、前置安装

### 1. PostgreSQL

已安装即可。建议配置：

- 管理员用户：postgres
- 管理员密码：postgres
- 端口：5432

### 2. Node.js LTS

管理员 PowerShell 在线安装：

```powershell
winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
```

安装后检查：

```powershell
node -v
npm -v
```

## 三、第一次启动顺序

在项目根目录依次双击或运行：

```txt
01_install_node_deps.bat
02_init_database.bat
03_start_all.bat
```

说明：

- `01_install_node_deps.bat`：安装 13 个 Node 项目的依赖。
- `02_init_database.bat`：创建 `benchmark_user`、`benchmark_mall`，并导入 schema 和 seed。
- `03_start_all.bat`：启动 11 个后端服务和 2 个前端服务。

## 四、日常使用

启动：

```txt
03_start_all.bat
```

健康检查：

```txt
05_health_check.bat
```

停止：

```txt
04_stop_all.bat
```

重置数据库：

```txt
02_init_database.bat
```

## 五、访问地址

```txt
用户端：http://localhost:3001
管理端：http://localhost:3002
API 健康检查：http://localhost:8080/health
```

内部服务端口：

```txt
auth-service       http://localhost:8001
user-service       http://localhost:8002
product-service    http://localhost:8003
inventory-service  http://localhost:8004
cart-service       http://localhost:8005
coupon-service     http://localhost:8006
order-service      http://localhost:8007
payment-service    http://localhost:8008
refund-service     http://localhost:8009
report-service     http://localhost:8010
gateway-service    http://localhost:8080
```

## 六、数据库连接

```txt
postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall
```

注意：本机非 Docker 版本端口是 `5432`。Docker 版本端口是 `55432`。

## 七、给 QualiBug 填写的信息

```txt
企业资料：docs/ 目录下的文档
用户端：http://localhost:3001
管理端：http://localhost:3002
API：http://localhost:8080
数据库：postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall
测试账号：docs/TEST_ACCOUNTS.md
```

不要把 `hidden_ground_truth/` 目录给 QualiBug。这个目录是评分答案集。

## 八、日志位置

启动后的日志在：

```txt
.runtime/windows/logs/
```

如果某个服务失败，先看对应的：

```txt
服务名.out.log
服务名.err.log
```

## 九、常见问题

### 1. psql 找不到

说明 PostgreSQL bin 目录没有加入 PATH。脚本会自动搜索：

```txt
C:\Program Files\PostgreSQL\<版本>\bin\psql.exe
```

如果仍找不到，说明 PostgreSQL 可能没有安装 Command Line Tools。

### 2. 数据库初始化失败

默认脚本认为 postgres 管理员密码是：

```txt
postgres
```

如果你安装 PostgreSQL 时设置了其他密码，用管理员 PowerShell 执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_db_windows.ps1 -PostgresPassword "你的密码"
```

### 3. 端口被占用

先运行：

```txt
04_stop_all.bat
```

它会清理这些端口：

```txt
3001, 3002, 8080, 8001-8010
```

### 4. 前端打不开

先看：

```txt
.runtime/windows/logs/customer-web.err.log
.runtime/windows/logs/admin-web.err.log
```

多数情况是 Node 依赖没有安装成功，重新运行：

```txt
01_install_node_deps.bat
```
