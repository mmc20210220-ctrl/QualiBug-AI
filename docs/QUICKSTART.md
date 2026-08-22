# QualiBug AI 快速开始（部署真值）

本文件是**部署快速开始**。目标：全新 clone 后 10 分钟内跑起可登录、可配置的完整系统。

## 前置条件

- Python 3.12
- Node.js 22（仅前端开发模式需要；生产部署由后端直接托管已构建的 SPA）
- 可选：Docker（见文末「容器部署」）

## 方式 A：本机开发启动（Windows / macOS / Linux）

```powershell
# 1. 安装产品包（后端 + CLI）
pip install .

# 2. 私有部署自检（推荐先执行）
qualibug-doctor

# 3. 配置环境变量（最少两项）
#    在仓库根目录创建 .env.local（启动时自动加载；已有进程环境变量优先）：
#      QUALIBUG_JWT_SECRET=<强随机值>
#      QUALIBUG_CRED_ENC_KEY=<强随机值>   # 凭据静态加密密钥
#    也可用 QUALIBUG_ENV_FILE 指向任意单一 env 文件。

# 4. 启动后端（固定端口 8088）
python -m ai_test_asset_center.private_pilot_entrypoint
# 或：qualibug-server

# 5. 启动前端开发服务器（固定端口 5174，仅开发模式）
cd frontend
npm ci
npm run dev
```

访问：

- 前端控制台：`http://127.0.0.1:5174`
- 后端健康检查：`http://127.0.0.1:8088/api/health`

> Windows 一键脚本：仓库根目录 `start_all.bat`（自动定位仓库、缺 JWT secret 时 fail-fast 并给出生成方法）。

## 方式 B：生产口径

生产环境不需要 Vite 开发服务器。构建一次 SPA，由后端在同一端口托管 UI + API：

```bash
cd frontend && npm ci && npm run build   # 产物在 frontend/dist
pip install .
QUALIBUG_FRONTEND_DIST=frontend/dist python -m ai_test_asset_center.private_pilot_entrypoint
```

## 容器部署

镜像内的 ui 阶段会自行编译前端，全新 clone 无需宿主机 Node：

```bash
export QUALIBUG_JWT_SECRET=...       # compose 缺省即拒绝启动
export QUALIBUG_CRED_ENC_KEY=...
docker compose up -d --build         # 根目录 Dockerfile：完整文档理解运行时（LibreOffice/OCR）
# 或精简版（无 OCR 栈）：deploy/docker-compose.private.yml
```

## 首次登录后的最小路径

1. 右上角选择/新建客户工作区
2. 「接入」页填 系统地址 + 测试凭据（高级项可折叠跳过）
3. 「企业资料」页连接在线资料源或上传文件
4. 「运行中心」通过运行前检查后启动检测；进度与取消在全局横幅

详细架构与评估口径见 `README.md` 与 `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`。
