# Phase60：企业试点运行时与私有化交付底座

## 目标

将 Phase59 的企业 TestOps 控制平面推进到可以给真实企业做私有化试点的运行形态。

本版本不重新实现业务知识、行业推断、探针、Oracle、证据、分诊或门禁；只补齐这些已有能力进入企业环境时所需的最小运行底座：项目隔离、连接器登记/同步、任务队列、审批、审计、私有服务入口和试点运营指标。

```text
企业导出资料 / 已有知识资产
→ 连接器登记与受控同步
→ 项目级知识资产
→ Phase59 控制平面
→ 任务队列 / 独立审批
→ 安全执行计划 / 风险与证据 / 发布门禁
→ 企业试点运营看板
```

## Ponytail 约束

- 新增一个运行时模块和一个极小的标准库 HTTP 服务，不引入工作流引擎、消息队列、ORM、前端框架或第二套业务规则库。
- 继续复用 `enterprise_knowledge_center`、`enterprise_testops_control_plane`、`release_risk_dashboard` 和现有发现引擎。
- 不为了“连接器完整”保存 Token、密码、API Key 或擅自抓取企业 SaaS 文档。
- 不为了“自动化完整”自动写入生产、自动造数或自动重放业务操作。

## 新增资产

项目目录：

```text
platform_workspace/<project>/enterprise_pilot_runtime/
platform_outputs/<project>/enterprise_pilot_runtime/
```

- `pilot_runtime_config.json`：企业/工作区/项目边界、私有部署配置、成员与策略。
- `connector_registry.json`：连接器元数据、凭证引用、同步状态；不保存导出正文或密钥。
- `task_queue.json`：持久化任务队列、幂等键、运行状态和结果摘要。
- `execution_approvals.json`：隔离数据计划、生产类环境任务等的独立审批。
- `runtime_audit_log.jsonl`：哈希链操作审计。
- `enterprise_pilot_overview.json`：试点运营总览。
- `pilot_success_scorecard.json`：知识资产、环境、任务、审计和连接器准备度。
- `private_deployment_manifest.json`：私有部署边界和目录。
- `enterprise_pilot_center.html`：中文试点运营中心。

## 连接器策略

支持登记的来源：

- 文件导出
- Confluence 导出
- 飞书导出
- Jira 导出
- 禅道导出
- GitLab Diff 导出
- OpenAPI 契约

当前版本的“同步”定义为：企业在内网或其官方客户端导出资料后，由平台通过 Phase58 的版本化知识接入层进行解析、去重、替代和关联。运行时不读取远程 URL，不解析 OAuth Token，也不把导出正文复制到连接器配置。

这是私有化 PoC 的最小可信交付方式：先把真实资料接进来并可审计，再逐个为客户优先级最高的系统接官方 API 连接器。

## 任务与审批

| 任务 | 默认行为 | 审批 |
|---|---|---|
| `control_plane_refresh` | 重建已存在的结构化资产 | 测试环境无需审批 |
| `environment_health` | 生成环境健康报告，默认不发网络请求 | 无需审批 |
| `safe_discovery_plan` | 生成 Probe/Oracle 执行计划，不隐式运行写请求 | 测试环境无需审批；生产类需安全审批且最终仍受生产保护拦截 |
| `release_gate` | 生成发布风险与门禁建议 | 无需审批 |
| `sandbox_data_setup_plan` | 仅生成隔离数据准备计划，不执行写入 | 需要独立审批 |

独立审批规则：提交人不能审批自己的任务；生产类环境必须由 `security_owner` 或 `admin` 审批；即使审批通过，运行时仍不会执行生产写入或生产缺陷发现。

## 私有服务

入口：

```bash
python -m ai_test_asset_center.private_pilot_service
```

- `GET /health`
- `GET /`、`GET /dashboard`：中文试点运营中心
- `GET /api/pilot/overview?project=<project>`
- `GET /api/pilot/tasks?project=<project>`
- `POST /api/pilot/config`
- `POST /api/pilot/connectors`
- `POST /api/pilot/connectors/sync`
- `POST /api/pilot/tasks`
- `POST /api/pilot/tasks/approve`
- `POST /api/pilot/tasks/run-next`

所有写接口要求反向代理注入：

```text
X-QualiBug-Actor: <企业用户标识>
X-QualiBug-Role: qa_engineer | qa_lead | project_owner | security_owner | testops_admin | admin
```

服务默认仅绑定 localhost。Docker 部署包同样仅映射到宿主机 `127.0.0.1`，企业应在前方配置 HTTPS、SSO/OIDC、网络 ACL、日志归集、备份与证书轮换。

## 试点成功指标

- 企业资料已形成可追溯知识资产。
- 至少一个连接器导出成功入库且可版本化。
- 至少一个环境健康检查通过。
- 一次控制平面刷新和一次安全发现计划成功执行。
- 隔离数据准备计划经独立审批。
- 审计哈希链有效。
- 缺陷发现、证据包、风险雷达与发布门禁可以复用已有链路。

该试点评分衡量接入、治理和受控运行准备度，不保证生产真实缺陷发现率、零缺陷或覆盖全部业务 Bug。
