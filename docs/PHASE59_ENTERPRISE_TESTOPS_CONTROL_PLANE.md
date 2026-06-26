# Phase59：企业 TestOps 控制平面

## 目标

将 Phase58 的“企业知识资产”转成可以在不同企业项目中复用的测试执行资产。它不重建业务知识、探针、Oracle、证据或报告系统，而是给这些已有能力增加统一的企业落地上下文：环境、测试数据、系统状态、跨系统链路、权限、缺陷质量、安全审计、解释和多行业评测。

`资料 → 企业知识资产 → 统一控制平面 → Probe / Oracle / 证据 / 分诊 / 发布门禁`

## Ponytail 约束

- 复用企业知识中心、行业推断、风险探针、保障覆盖、证据包、缺陷生命周期和发布看板。
- 只增加一个编排模块 `enterprise_testops_control_plane.py`，不新建平行规则库。
- 不为“看起来完整”实现远程数据库写入、凭证解析器或生产写入执行器。
- 安全、错误处理、数据隔离、审计和验证不会因代码简化被删除。

## 输出资产

控制平面目录：

```text
platform_workspace/<project>/enterprise_testops_control_plane/
platform_outputs/<project>/enterprise_testops_control_plane/
```

主要 JSON：

- `test_data_orchestration.json`：数据依赖图、准备步骤、隔离策略、自动准备比例、人工缺口。
- `environment_health_report.json`：dev/test/uat/prod-like 健康、缺失项、环境差异和建议动作。
- `database_validation_config.json`：表/字段/主键映射、只读查询模板、状态/金额/租户/审计断言。
- `system_state_evidence.json`：接口、数据库、审计和异步状态差异证据；内置 SQLite 只读适配器，其他数据库通过同一查询契约接入。
- `business_journey_graph.json`、`cross_system_journey_report.json`：跨系统业务链路与断言结果。
- `permission_matrix.json`、`permission_risk_report.json`：角色、资源、字段、组织和租户边界。
- `defect_quality_report.json`：可信度、影响、证据、复现、环境/数据分流和重复聚类。
- `issue_lifecycle.json`、`fix_verification_result.json`：缺陷草稿、Owner 建议、回归探针和复测计划。
- `security_audit_report.json`：凭证引用、脱敏、生产保护、操作审计、哈希链校验。
- `explainable_test_assets.json`：为什么生成、违反规则、证据、严重级别和置信度原因。
- `multi_industry_benchmark_report.json` 与 HTML：七行业样例的文档/风险种子覆盖代理指标。

## 十项能力如何收敛

| 目标 | 最小实现 | 复用链路 |
|---|---|---|
| 测试数据自治 | 从接口写入前置和表依赖推导准备计划、隔离 tenant/run_id、清理策略与数据健康检查 | 企业知识资产、风险计划 |
| 多环境管理 | 统一环境配置与健康报告，健康差异进入发布判断 | 项目配置、发布看板 |
| 数据库/系统状态 | 生成数据库映射和只读断言；接口成功但状态错误可产生 P0/P1 证据 | Oracle、证据包 |
| 跨系统 Journey | 从对象/模块/状态依赖生成 Journey 图和跨系统 Oracle | 行业推断、风险雷达 |
| 权限/租户 | 从权限矩阵和接口生成匿名、IDOR、跨租户、字段权限探针 | 企业知识中心、隔离推理 |
| 去噪 | 计算可信度、业务影响、证据、复现并按证据签名合并 | 缺陷报告、分诊矩阵 |
| 修复闭环 | 复用生命周期与确认 Bug 学习闭环，补充 Owner 建议和复测计划 | `issue_lifecycle_center`、`fix_verification_loop` |
| 企业安全 | 凭证引用、派生资产脱敏、RBAC、审计哈希链、生产写入保护 | 知识接入治理、风险策略 |
| 可解释资产 | Probe/Bug/雷达/门禁都输出规则、接口、对象、状态机和 Oracle 来源 | 知识资产关系图 |
| 多行业 Benchmark | 基于七行业公开样例跑同一推断/风险/Oracle 引擎 | Phase57 多行业推断 |

## 安全边界

- 默认只读：网络验证是 opt-in 的 `/health` GET；安全执行默认 GET/HEAD/OPTIONS。
- 写入前置数据只生成计划；仅在隔离测试环境中通过受控业务 API 或数据库 seed 执行。
- 生产或生产类环境禁止自动造数、补偿、重放、取消和破坏性测试。
- 凭证只保存 `vault:` 等引用，报告自动脱敏。
- 数据库验证仅支持适配器或本地 SQLite 只读 `SELECT`；`state_validate` 只接受单条参数化 SELECT，拒绝写入、多语句和危险 SQL；不在模块中擅自打开远程数据库。
- 环境不可测、账号失效、测试数据不足会进入去噪分流，不计入高价值业务缺陷。

## 页面/API

页面：`enterprise_testops_center.html`，中文展示测试环境、数据自治、系统状态、Journey、权限、缺陷生命周期、安全审计、解释和 Benchmark。

控制入口：

```python
operate_enterprise_testops(project, action, payload, actor)
```

支持：`view`、`rebuild`、`save_environment`、`environment_health`、`test_data_plan`、`state_validate`、`permission_matrix`、`journey_graph`、`defect_quality`、`issue_lifecycle`、`explainability`、`security_audit`、`benchmark`。

配置、重建和审计操作要求 `project_owner`、`qa_lead`、`security_owner`、`testops_admin` 或 `admin`。

## 运行

```bash
python -m ai_test_asset_center.enterprise_testops_control_plane --project <project> --rebuild
python -m ai_test_asset_center.enterprise_testops_control_plane --demo
python -m ai_test_asset_center.enterprise_testops_control_plane --benchmark --project benchmark_demo
```

## 验证边界

Benchmark 的“发现率、误报率、S/A 高价值率、Oracle 覆盖率、上下文命中率、证据包率、修复闭环率”目前均以公开文档和已知高价值风险种子的可重复代理指标计算，不声称等价于任何客户生产环境真实缺陷发现率。
