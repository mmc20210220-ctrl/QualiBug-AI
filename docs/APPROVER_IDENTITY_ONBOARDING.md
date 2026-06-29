# 审批身份接入指南

## 目标

本指南说明如何为 QualiBug 项目接入审批身份输入，让部署漂移审批从“手工传 `approver_context`”升级为“基于项目成员表、租户 RBAC、SSO claims 自动解析”。

接入完成后，可以获得以下能力：

- 自动解析审批人的项目、租户、环境绑定
- 自动选择更可信的身份来源，例如 `sso_claims`
- 在审批前预览“某个人为什么能批/不能批”
- 在 onboarding 阶段发现身份输入缺失并给出修复建议

## 相关入口

当前已经提供 5 个正式入口：

1. `identity-status`
2. `init-approver-identity-template`
3. `save-approver-identity`
4. `resolve-approver`
5. `approve-drift`

统一入口文件：

- `ai_test_asset_center/real_project_onboarding.py`

核心解析器：

- `ai_test_asset_center/approver_identity_resolver.py`

## 文件位置

审批身份输入默认位于：

- `platform_inputs/<project_id>/approver_identity_registry.json`
- `platform_inputs/<project_id>/approver_project_members.json`
- `platform_inputs/<project_id>/approver_tenant_rbac.json`
- `platform_inputs/<project_id>/approver_sso_claims.json`

说明：

- 可以只使用总的 `approver_identity_registry.json`
- 也可以拆成 3 份独立文件
- 解析器会自动合并这些来源

## 推荐接入流程

### 1. 先看状态

先检查当前项目有没有身份输入：

```bash
python ai_test_asset_center/real_project_onboarding.py identity-status your_project
```

返回码：

- `0`：已存在身份输入
- `2`：缺少身份输入

如果还没有接入，输出里会包含建议命令：

```bash
python ai_test_asset_center/real_project_onboarding.py init-approver-identity-template your_project --overwrite
```

### 2. 初始化模板

生成标准模板文件：

```bash
python ai_test_asset_center/real_project_onboarding.py init-approver-identity-template your_project --overwrite
```

模板会自动填入当前项目的：

- `project_id`
- `deployment_scope_id`
- `environment_class`

并生成示例角色：

- `project_owner_demo`
- `qa_lead_demo`
- `tenant_admin_demo`
- `security_owner_demo`

### 3. 替换为真实身份

将模板中的示例 actor 和 role 替换为真实成员。

可选的三类输入：

- `project_members`
- `tenant_rbac`
- `sso_claims`

推荐优先补齐 `sso_claims`，因为它的身份可信级别更高。

### 4. 用命令写入

如果你不想手工编辑文件，也可以直接写入：

```bash
python ai_test_asset_center/real_project_onboarding.py save-approver-identity your_project ^
  --project-members-json "[{\"actor_id\":\"alice\",\"roles\":[\"project_owner\"],\"project_ids\":[\"your_project\"],\"environment_classes\":[\"staging\"]}]" ^
  --tenant-rbac-json "[{\"actor_id\":\"alice\",\"roles\":[\"tenant_admin\"],\"tenant_ids\":[\"tenant_a\"]}]" ^
  --sso-claims-json "[{\"actor_id\":\"alice\",\"roles\":[\"project_owner\"],\"project_ids\":[\"your_project\"],\"tenant_ids\":[\"tenant_a\"],\"environment_classes\":[\"staging\"],\"identity_source\":\"sso_claims\"}]"
```

也支持从文件读取：

```bash
python ai_test_asset_center/real_project_onboarding.py save-approver-identity your_project ^
  --project-members-file D:\data\project_members.json ^
  --tenant-rbac-file D:\data\tenant_rbac.json ^
  --sso-claims-file D:\data\sso_claims.json
```

### 5. 预览解析结果

在真正审批之前，建议先预览解析结果：

```bash
python ai_test_asset_center/real_project_onboarding.py resolve-approver your_project --approver alice --approver-role project_owner
```

或者使用更轻量的状态预览：

```bash
python ai_test_asset_center/real_project_onboarding.py identity-status your_project --approver alice --approver-role project_owner
```

你会看到：

- `resolved_approver_context`
- `approval_validation`
- `required_roles`
- `identity_registry_paths`

### 6. 再执行审批

确认解析结果无误后，再执行真实审批：

```bash
python ai_test_asset_center/real_project_onboarding.py approve-drift your_project --approver alice --approver-role project_owner --unlock-level limited
```

## 输入格式示例

### project_members

```json
[
  {
    "actor_id": "alice",
    "roles": ["project_owner"],
    "project_ids": ["your_project"],
    "environment_classes": ["staging", "sandbox"]
  },
  {
    "actor_id": "bob",
    "roles": ["qa_lead"],
    "project_ids": ["your_project"],
    "environment_classes": ["staging"]
  }
]
```

### tenant_rbac

```json
[
  {
    "actor_id": "alice",
    "roles": ["tenant_admin"],
    "tenant_ids": ["tenant_a"]
  }
]
```

### sso_claims

```json
[
  {
    "actor_id": "alice",
    "roles": ["project_owner"],
    "project_ids": ["your_project"],
    "tenant_ids": ["tenant_a"],
    "environment_classes": ["staging"],
    "identity_source": "sso_claims"
  }
]
```

## 身份来源优先级

当同一审批人在多个来源中都命中时，系统会选择更可信的来源作为最终 `identity_source`：

1. `admin_override`
2. `customer_hub`
3. `sso_claims`
4. `tenant_rbac`
5. `api_token`
6. `local_config`

说明：

- 系统会保留 `resolution_sources`，用于审计所有命中的来源
- 最终 `identity_source` 只表示本次最可信的来源

## 常见排查

### 1. `identity-status` 返回 2

说明当前项目还没有审批身份输入。

处理方式：

```bash
python ai_test_asset_center/real_project_onboarding.py init-approver-identity-template your_project --overwrite
```

### 2. `resolve-approver` 显示 `project_matches=false`

说明审批人的 `project_ids` 或 `project_bindings` 没有覆盖当前项目。

检查：

- `project_members`
- `sso_claims`

### 3. `scope_matches=false`

说明审批人的租户绑定没有覆盖当前 `deployment_scope_id`。

检查：

- `tenant_rbac`
- `sso_claims`

### 4. `environment_matches=false`

说明审批人的环境绑定没有覆盖当前 `environment_class`。

检查：

- `environment_classes`
- `environment_bindings`

### 5. `identity_source_allowed=false`

说明当前场景要求更可信的身份来源。

建议：

- 优先补齐 `sso_claims`
- 高风险环境尽量不要只依赖 `local_config`

## 推荐实践

- 私有部署先接 `project_members + tenant_rbac`
- 企业环境优先接 `sso_claims`
- 所有审批前先跑一次 `resolve-approver`
- 运维排查先用 `identity-status`
- 新项目第一次接入先跑模板初始化，再替换成真实成员

## 最短路径

如果你想最快跑通一条链路，按下面顺序即可：

1. `identity-status your_project`
2. `init-approver-identity-template your_project --overwrite`
3. 编辑模板文件为真实成员
4. `resolve-approver your_project --approver alice --approver-role project_owner`
5. `approve-drift your_project --approver alice --approver-role project_owner`
