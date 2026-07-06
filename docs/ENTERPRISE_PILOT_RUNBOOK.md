# QualiBug 企业试点运行手册

本手册描述受控 Campaign 的最小可交付流程。它不替代企业自己的变更、数据和环境审批制度；QualiBug 只记录并校验已批准的事实。

## 1. 前置条件

- 已配置 `QUALIBUG_API_TOKEN`。
- 需要执行目标流量时，已配置 `QUALIBUG_ALLOWED_TARGET_ORIGINS`，值为逗号分隔的完整 Origin，例如 `https://test.example.com`。
- 试点项目具有可用的 API 资料、范围标识和环境引用。
- 任何一次写入型测试数据操作均在隔离环境中完成，并由受信任的数据适配器记录创建与清理回执。

## 2. 登记不可变资料

调用 `POST /v1/source-assets/register`，提供：

- `project_id`
- 稳定的 `source_id`
- `source_type`，API 资料使用 `openapi`
- 原始资料内容
- 可选的文件名、外部引用和元数据

响应包含：

- `source_id`
- `source_hash`
- `source_version_id`
- `source_origin=registered_source_registry`

后续扫描可只传这份 manifest。系统会从已登记来源版本读取同一份内容，而不是依赖临时上传文本。

## 3. 生成计划型 Campaign

调用 `POST /v1/scans`，不传 `base_url`，提供：

- 项目 ID
- `scope_id`
- `environment_ref`
- 已登记 `source_manifest`
- 可选 PRD
- 测试数据策略

计划型 Campaign 不产生目标流量。预期结果通常为：

- `runtime_contract.status=plan_only`
- `release_gate.verdict=not_ready`
- 生成 Campaign、行为切片、覆盖缺口和证据包

`not_ready` 不是失败结论，表示尚未满足发布判断条件。

## 4. 绑定测试数据回执

写入型测试策略不能只填写任意字符串。必须提供与同一 Campaign、范围和环境绑定的回执：

- `reuse_verified_existing`：数据来源回执
- `create_disposable`：隔离范围、创建回执、清理回执、显式写入批准
- `approved_fixture_setup`：Fixture 引用、Fixture 回执、创建回执、清理回执、显式写入批准

回执只记录操作元数据、作用域和操作引用，不应包含用户数据、凭据或业务记录内容。

## 5. 签发执行批准

目标执行前，批准方调用 `POST /v1/execution-approvals`。批准绑定：

- Campaign ID
- 范围与环境
- 来源 SHA-256
- 目标 Origin
- 执行模式
- 过期时间

默认执行模式是 `safe_read_only`。`approved_sandbox_write` 必须在批准中明确声明，且只能用于隔离环境。

没有执行批准时，系统可以保留计划，但不会产生 API 或浏览器目标流量。

## 6. 执行受控 Campaign

再次调用 `POST /v1/scans`，提供：

- 已登记来源 manifest
- 范围、环境和测试数据合同
- 已批准的 `base_url`
- `execution_approval_id`
- `execution_mode`

执行前系统校验：

1. 来源哈希是否匹配；
2. 目标 Origin 是否在允许列表内；
3. Campaign、范围、环境和批准是否一致；
4. 批准是否仍在有效期内；
5. 仅允许资料绑定的行为切片进入执行。

## 7. 校验证据

每轮扫描会生成证据包。调用：

`GET /v1/evidence-bundles/{project_id}/{bundle_id}/verify`

系统校验每个脱敏 artifact 的 SHA-256 以及 bundle manifest 的完整性。证据包存在不等于缺陷已确认；confirmed 仍要求完整执行回执与断言。

## 8. 读取发布结论

扫描结果中的 `release_gate` 是唯一权威发布结论。它会明确返回：

- `pass / release_ready`
- `not_ready / inconclusive`
- `fail / blocked`

下列任一条件会阻止通过：

- Campaign 阻塞或覆盖递延；
- 运行合同或执行批准未满足；
- 真实执行未完成；
- 测试数据回执未验证；
- 证据包不可验证；
- 覆盖缺口仍存在；
- 存在 confirmed P0；默认情况下 confirmed P1 也阻止通过。

## 9. 当前产品边界

当前主链已支持来源登记、Campaign、测试数据回执、执行批准、证据包、发布结论与 OpenAPI 版本差异。仍在继续完善：

- 历史私有服务中固定 API 回退的物理删除；
- UI 路由与来源登记页面的完全接入；
- 企业 SSO、RBAC、SCIM 与多租户隔离；
- 对象存储、KMS、长期审计与保留策略；
- Git、Jira、Confluence 等真实连接器；
- 浏览器 Trace 直接进入证据包；
- CI 实际运行记录与生产部署验证。
