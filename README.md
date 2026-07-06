﻿# QualiBug AI Enterprise Edition

**Enterprise Business-Quality Assurance Platform — AI-powered bug discovery across any industry**

QualiBug AI 发现普通端点测试经常遗漏的高价值缺陷：跨系统状态漂移、跨视图核对错误、租户隔离失败、生命周期回归、财务守恒违规和 unsafe 发布风险。

---

## 版本信息

**当前版本**: 95.0.0（Phase106 前端工程化 · 企业质量指挥中心 HTTP API）

证据管道保持四层状态保留（自 Phase92A 起持续生效）：

```
Raw Probe Evidence → Normalized Runtime Evidence → Semantic Verification Evidence → Business Finding Contract
```

---

## 快速开始

### 本地安装

```bash
# 1. 克隆项目
git clone https://github.com/qualibug/qualibug-ai.git
cd qualibug-ai

# 2. 安装依赖
pip install -r requirements.txt
pip install -e .

# 3. 配置环境变量
cp .env.local.example .env.local
# 编辑 .env.local 配置你的 LLM API

# 4. 运行测试
python -m pytest tests/test_phase95_runtime_evidence_scoreboard.py -v
# 或运行完整测试套件
python -m pytest tests/ -q

# 5. 启动服务
# 后端固定端口：8088
python -m ai_test_asset_center.private_pilot_service
# 前端固定端口：5174
cd frontend && npm run dev
# 或使用 CLI（发布验证、扫描等）
qualibug verify-release
```

### Docker部署

```bash
# 构建镜像
docker build -t qualibug-ai .

# 使用docker-compose启动
docker-compose up -d

# 查看日志
docker-compose logs -f qualibug

# 访问服务
# http://localhost:5000
```

---

## 核心功能

### 证据管道 (Phase92A)

| 组件 | 功能 | 文件 |
|------|------|------|
| Evidence Normalizer | 原始探针证据归一化 | evidence_normalizer.py |
| Business Evidence Enricher | 业务证据富化 | business_evidence_enricher.py |
| Discovery Finding Gate | 双层门控 | discovery_finding_gate.py |
| Adversarial Validator | 对抗性验证 | business_adversarial_validator.py |
| Finding Registry | 发现注册中心 | business_finding_registry.py |

### 双层门控机制

1. **Runtime Evidence Gate** — 验证探针证据可追溯性
   - 检查原始探针调用存在性
   - 验证语义证据引用
   - 确保运行时裁决可追溯

2. **Business Evidence Gate** — 验证业务合同完整性
   - 实体绑定完整性
   - Before/After快照存在性
   - 动作证据引用
   - 观察者证据
   - Cleanup状态

### 安全保证

- ✅ **禁止自动确认**: CANDIDATE → CONFIRMED 需人工审核
- ✅ **生产模式无HTTP**: `QUALIBUG_PRODUCTION=1` 时零外部请求
- ✅ **禁止伪造证据**: Evidence enricher 从不创建虚假数据
- ✅ **禁止绕过门控**: 证据门控不可跳过
- ✅ **语义裁决保留**: Stage_verify 裁决永不丢弃
- ✅ **Cleanup阻塞**: 脏环境阻塞高风险发现

---

## 命令行工具

```bash
# 检查本地与 LLM 配置
qualibug doctor

# 运行发布验证
qualibug verify-release

# 启动私有服务（无独立 server 入口，直接运行模块）
python -m ai_test_asset_center.private_pilot_service

# 运行发现引擎（--prd / --api 为必需参数）
qualibug discover --prd "path/to/prd.md" --api "path/to/api.md"
```

---

## API端点

私有服务 `private_pilot_service`（默认端口 8088）：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查（含真实 LLM 连通状态，未连通时为 offline） |
| `/api/pilot/status` | GET | 服务状态 |
| `/api/scan/run` | POST | 运行扫描 |
| `/api/pilot/config` | GET/POST | 配置管理 |
| `/api/knowledge/ingest` | POST | 知识导入 |
| `/api/settings/save` | POST | 系统设置 |

前端联调 API 面（Phase104A，`phase104_command_center_http_api`，默认端口 8088）使用 `/api/v1/*` 前缀，详见 `PHASE104A_COMMAND_CENTER_HTTP_API_README.md`。

---

## 项目结构

```
qualibug-ai/
├── ai_test_asset_center/     # 核心引擎
│   ├── discovery_engine.py   # 发现引擎
│   ├── discovery_finding_gate.py  # 双层门控
│   ├── evidence_normalizer.py     # 证据归一化
│   ├── business_evidence_enricher.py  # 业务证据富化
│   ├── private_pilot_service.py   # 私有服务
│   ├── product_ui.py              # Web UI
│   └── ...                        # 其他模块
├── aitestops/               # CLI工具
│   ├── cli.py              # 命令行入口
│   └── release_verifier.py  # 发布验证
├── mes_target/              # MES演示目标
├── tests/                   # 测试套件
├── requirements.txt         # 依赖清单
├── Dockerfile              # Docker镜像
├── docker-compose.yml      # Docker编排
└── README.md               # 本文档
```

---

## 配置

### 环境变量 (.env.local)

代码只读取 `LLM_*`（OpenAI 风格的 `OPENAI_*` 名称不被读取）。完整参考见 `.env.local.example`。

```bash
# LLM配置 (必填) — 代码读取 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=your-api-key
LLM_MODEL=your-deepseek-model
LLM_TEMPERATURE=0.1
LLM_TIMEOUT_SECONDS=300
LLM_THINKING_MODE=disabled
LLM_RESPONSE_FORMAT=json_object

# 服务配置
QUALIBUG_PROJECT=real_project_demo
QUALIBUG_PORT=8088        # 默认 8088
QUALIBUG_PRODUCTION=0     # 设为 1 时进入生产模式，禁止所有外部 HTTP 写入
```

---

## 测试

```bash
# 运行核心测试
python -m pytest tests/test_phase95_runtime_evidence_scoreboard.py -v
python -m pytest tests/test_real_project_discovery_contract.py -v

# 运行完整测试套件
python -m pytest tests/ -v --cov=ai_test_asset_center

# 生成HTML报告
python -m pytest tests/ --html=report.html
```

---

## 许可证

Proprietary - QualiBug Team

---

## 支持

- 文档: `docs/`
- 问题: GitHub Issues
- 邮件: team@qualibug.com

