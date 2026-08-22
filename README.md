# QualiBug AI

**Enterprise software behavior-space infrastructure — AI-powered bug discovery across industries**

QualiBug AI 把企业系统的 Actor、状态、数据、规则与真实执行轨迹映射为可计算、可验证、可演进的行为空间模型，并在受治理的非生产环境中自动发现高价值缺陷。

典型发现范围包括：跨系统状态漂移、跨视图核对错误、租户隔离失败、生命周期回归、守恒违规，以及不安全发布风险。

> **Bug** = 观察到的行为与期望行为之间、可证据化复现的偏差。  
> 不是爬虫，不是扫描器口号；产品视觉与文案不使用昆虫 / 爬虫语义。

---

## 产品口径

| 项 | 值 |
|---|---|
| 产品版本 | `95.0.0` |
| 后端端口 | `8088` |
| 前端端口 | `5174` |
| 标准健康检查 | `GET /api/health` |
| 兼容健康检查 | `GET /health` |
| 正式后端入口 | `ai_test_asset_center.private_pilot_entrypoint`（`qualibug-server`） |
| 正式客户前端 | `frontend/` React 控制台 |
| Docker 镜像标签 | `qualibug-ai:95.0.0-private-pilot` |

正式客户前端：`frontend/` React 控制台；正式后端入口：`ai_test_asset_center.private_pilot_entrypoint`（`qualibug-server`）。

`backend/main.py` 仅作兼容 / 实验接口，**不是**正式真实执行主入口。

---

## 快速开始

### 环境要求

- Python `>= 3.11`
- Node.js（前端开发）
- 可选：Docker / Docker Compose

### 本地安装

```bash
# 1. 克隆仓库
git clone https://github.com/mmc20210220-ctrl/QualiBug-AI.git
cd QualiBug-AI

# 2. 安装 Python 依赖
pip install -r requirements.txt
pip install -e .

# 3. 配置环境变量
cp .env.local.example .env.local
# 编辑 .env.local：配置 LLM API，并设置 QUALIBUG_JWT_SECRET

# 4. 私有部署自检（推荐先执行）
qualibug-doctor

# 5. 启动后端（固定端口 8088）
python -m ai_test_asset_center.private_pilot_entrypoint
# 或
qualibug-server

# 6a. 生产口径：构建 SPA 后由后端 8088 同端口托管（推荐）
cd frontend && npm ci && npm run build && cd ..
# 以生产方式启动（QUALIBUG_FRONTEND_DIST 指向构建产物）：
QUALIBUG_FRONTEND_DIST=frontend/dist python -m ai_test_asset_center.private_pilot_entrypoint

# 6b. 开发模式（可选，仅本地开发：Vite dev server 固定端口 5174）
cd frontend
npm run dev
```

访问：

- 生产/容器部署：`http://127.0.0.1:8088`（UI + API 同端口）
- 本机开发模式前端控制台：`http://127.0.0.1:5174`（`/api` 代理到 8088）
- 后端健康检查：`http://127.0.0.1:8088/api/health`

### 一键自检

```bash
qualibug-doctor              # 只读诊断（JSON）
qualibug-doctor --compact    # 紧凑输出，便于 CI
qualibug-doctor --output     # 写出交付用诊断报告
```

### Docker 部署

```bash
# 构建
docker build -t qualibug-ai:95.0.0-private-pilot .

# 启动（两个密钥都是必填，缺任一都会 fail-closed）
# Linux / macOS:
export QUALIBUG_JWT_SECRET=your-high-entropy-secret
export QUALIBUG_CRED_ENC_KEY=your-high-entropy-secret
# Windows PowerShell:
#   $env:QUALIBUG_JWT_SECRET="your-high-entropy-secret"
#   $env:QUALIBUG_CRED_ENC_KEY="your-high-entropy-secret"
docker-compose up -d

# 日志与健康检查
docker-compose logs -f qualibug
curl http://127.0.0.1:8088/api/health
```

容器默认映射到宿主机 `127.0.0.1:8088`。公开绑定需显式开启，并置于企业反向代理之后。更完整的私有部署说明见 [`deploy/README.md`](deploy/README.md)。

容器环境变量口径：

| 变量 | 是否必填 | 说明 |
|---|---|---|
| `QUALIBUG_JWT_SECRET` | 必填 | 租户 JWT 签发密钥 |
| `QUALIBUG_CRED_ENC_KEY` | 必填 | 凭据静态加密主密钥。镜像内置 `QUALIBUG_REQUIRE_CREDENTIAL_ENCRYPTION=1`，缺失则拒绝启动 |
| `QUALIBUG_PRIVATE_ROOT` | 镜像已设 `/app` | 运行态状态根目录，与挂载卷对齐 |
| `QUALIBUG_DISABLE_SANDBOX_WRITE` | 可选 | 操作员写入熔断开关。置 `1` 时在发出请求前阻断所有写探针 |

镜像**不再**设置 `QUALIBUG_PRODUCTION`。该变量同时被 `sandbox_write_executor_base._production_mode()` 当作全局写锁读取，在镜像里置 1 会让全部受治理写探针失效。目标写安全由 `target_policy.py` 按项目声明的 `environment_type` 判定（生产与未知类型 fail-closed），不由部署级开关决定。需要整体禁写请用 `QUALIBUG_DISABLE_SANDBOX_WRITE=1`。

---

## 正式产品主线

1. **Settings** — 维护项目、服务、鉴权与数据库配置  
2. **Enterprise Materials** — 导入企业资料，沉淀知识资产  
3. **Enterprise Campaigns** — 统一运行中心：发起标准扫描或受控执行  
4. **Dashboard / Findings / Evidence Chain** — 查看真实结果与证据链  
5. **Internal Clues** — 查看未通过客户交付门禁的内部线索及缺失原因  
6. **Release Gate** — 发布门禁与交付就绪判断  

前端开发细节见 [`frontend/README.md`](frontend/README.md)。

---

## 核心能力

### 行为空间与发现主线

- 从企业资料与接口文档构建 **Behavior IR**（行为中间表示）
- 编译测试义务（Test Obligations）与可执行实验（Executable Experiments）
- 在受治理沙箱中执行读写探针，产出可追溯证据与缺陷候选
- 仅通过客户交付门禁（Customer Delivery Gate）的结果才可对外交付

### 证据管道

```text
Raw Probe Evidence
  → Normalized Runtime Evidence
  → Semantic Verification Evidence
  → Business Finding Contract
```

关键原则：

- 证据可格式化展示，可生成明确标注的操作指引
- **不得**推断请求体、凭证、业务规则、表名 / SQL 或影响结论
- 合成指引不能替代真实执行证据，也不能满足客户交付门禁

### 执行治理

- 仅对**显式声明的非生产**目标自动执行读写探针
- 生产与未知环境类型对写操作 **fail-closed**
- 每次写操作必须经过治理沙箱，并产出 before/after 观察、清理结果与审计回执
- 只读模式可作为操作员熔断开关，在发请求前阻断写入

### 质量真相口径

- 产品缺陷真相以正式客户可交付结果为准，不以内部候选 / 漏斗计数冒充召回率或商业能力
- 商业推广所需的外部评测证据缺失时保持 `NOT_MEASURED`，不抬高宣称等级

---

## CLI 入口

| 命令 | 作用 |
|---|---|
| `qualibug-server` | 启动私有服务（正式后端入口） |
| `qualibug-doctor` | 安装 / 交付现场自检 |
| `qualibug` | 通用 CLI（含发布验证等） |
| `qualibug-acceptance-smoke` | 私有试点验收冒烟 |
| `qualibug-discover-chain` | 发现运行并校验主证据链 |
| `qualibug-pilot-chain` | 试点运行时任务 + 主证据链校验 |

---

## 仓库结构（摘要）

```text
ai_test_asset_center/   # 产品运行时、发现主线、证据与治理
frontend/               # 正式客户 React 控制台（端口 5174）
aitestops/              # CLI 与运维工具入口
deploy/                 # 私有部署包与环境模板
docs/                   # 架构、运行手册与目标规格
tests/                  # 自动化测试
tools/                  # 评测与运维辅助脚本
benchmark/              # 可见基准材料（不含评测私有 GT）
```

---

## 文档索引

| 文档 | 说明 |
|---|---|
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | 快速上手补充 |
| [`docs/API.md`](docs/API.md) | HTTP API |
| [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md) | 运维手册 |
| [`docs/ENTERPRISE_PILOT_RUNBOOK.md`](docs/ENTERPRISE_PILOT_RUNBOOK.md) | 企业试点手册 |
| [`docs/private_pilot_doctor.md`](docs/private_pilot_doctor.md) | Doctor 诊断说明 |
| [`deploy/README.md`](deploy/README.md) | 私有部署安全边界与启动 |
| [`frontend/README.md`](frontend/README.md) | 前端本地运行与 CI 门禁 |
| [`docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`](docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md) | 发现能力目标与验收门禁 |
| [`docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`](docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md) | 能力架构规格 |

开发代理约束与运行时契约见根目录 [`AGENTS.md`](AGENTS.md)。

---

## 设计原则

1. **Fail Fast** — 错误不得被静默吞掉  
2. **Fix the Cause** — 修根因，不糊表面补丁  
3. **Make It Observable** — 关键路径可追溯、可诊断  
4. **Industry Neutral** — 全行业适配，禁止客户业务硬编码  
5. **No Fake Bugs** — 没有真实执行证据的缺陷不得交付  
6. **Living Docs** — 产品方向变更时同步更新文档  

---

## 许可证

Proprietary. 未授权不得复制、分发或商用。
