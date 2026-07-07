# QualiBug AI Enterprise Edition

**Enterprise Business-Quality Assurance Platform — AI-powered bug discovery across any industry**

QualiBug AI 发现普通端点测试经常遗漏的高价值缺陷：跨系统状态漂移、跨视图核对错误、租户隔离失败、生命周期回归、财务守恒违规和 unsafe 发布风险。

---

## 版本信息

**当前版本**: 95.0.0（Phase106 前端工程化 · 企业质量指挥中心 HTTP API）

私有部署统一口径：

- 产品版本：`95.0.0`
- 默认后端端口：`8088`
- 正式客户前端：`frontend/` React 控制台
- 标准健康检查：`/api/health`
- 兼容健康检查：`/health`
- 标准私有服务入口：`ai_test_asset_center.private_pilot_entrypoint`
- Docker 镜像标签：`qualibug-ai:95.0.0-private-pilot`
- `backend/main.py` 仅保留为兼容/实验接口，不是正式真实执行主入口

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

# 4. 私有部署自检（推荐先执行）
qualibug-doctor
# 如需验证 runtime patch 实际安装状态：
qualibug-doctor --install-patches

# 5. 运行测试
python -m pytest tests/test_phase95_runtime_evidence_scoreboard.py -v
# 或运行完整测试套件
python -m pytest tests/ -q

# 6. 启动私有服务
# 后端固定端口：8088
python -m ai_test_asset_center.private_pilot_entrypoint
# 或使用安装后的脚本入口
qualibug-server

# 7. 启动正式客户前端（React 控制台）
# 前端固定端口：5174
cd frontend && npm run dev
# 或使用 CLI（发布验证、扫描等）
qualibug verify-release
```

正式产品主线为：

1. `frontend/src/pages/Settings.tsx` 维护项目、服务、鉴权和数据库配置
2. `frontend/src/pages/EnterpriseMaterials.tsx` 导入企业资料并沉淀知识资产
3. `frontend/src/pages/EnterpriseCampaigns.tsx` 作为统一运行中心发起标准扫描或受控执行
4. `frontend/src/pages/Dashboard.tsx`、`Findings.tsx`、`EvidenceChain.tsx` 查看真实结果与证据链

Phase105 静态前端产物保留为演示包、预览包和导出资产，不再作为正式产品主界面。

### 私有部署 Doctor

`qualibug-doctor` 输出 JSON 诊断，便于客户现场安装、自检和交付排障。默认模式为只读诊断，不要求 HTTP 服务已经启动。

诊断范围包括：

- 产品版本、Phase、默认端口、标准 health path；
- private-pilot patch 模块是否可导入；
- runtime patch 当前安装状态；
- 凭证加密 key 来源、前端是否只返回 masked refs；
- Browser UI Smoke 是否开启、Playwright 是否可用、UI base URL 环境变量；
- scan context contract 是否完整，包含 source manifest、scan body 准备、campaign context 构造；
- `/api/health` payload 预览。

```bash
# 只读自检
qualibug-doctor

# 紧凑 JSON，便于 CI 或脚本解析
qualibug-doctor --compact

# 安装 runtime patches 后再报告状态
qualibug-doctor --install-patches
```

### Docker 部署

```bash
# 构建镜像
docker build -t qualibug-ai:95.0.0-private-pilot .

# 使用 docker-compose 启动
docker-compose up -d

# 查看日志
docker-compose logs -f qualibug

# 访问服务（宿主机端口 5000 → 容器端口 8088）
# http://localhost:5000
# http://localhost:5000/api/health
```

Docker 容器内统一监听 `8088`，`docker-compose.yml` 默认映射为 `5000:8088`，因此本机演示仍访问 `http://localhost:5000`。

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
| Browser UI Smoke | 页面可达性、console/network 错误、截图/HAR 证据 | browser_ui_smoke.py |

### 双层门控机制

1. **Runtime Evidence Gate** — 验证探针证据可追溯性
   - 检查原始探针调用存在性
   - 验证语义证据引用
   - 确保运行时裁决可追溯

2. **Business Evidence Gate** — 验证业务合同完整性
   - 实体绑定完整性
   - Before/After 快照存在性
   - 动作证据引用
   - 观察者证据
   - Cleanup 状态

### 浏览器 UI Smoke 层

浏览器 UI 层默认关闭，不影响 API 扫描主链路。打开后，patched private pilot scan 会额外采集：

- 页面可达性与状态码；
- console error / warning；
- failed network request；
- 页面截图；
- HAR 证据文件。
