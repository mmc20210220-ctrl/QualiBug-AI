# 在线企业资料源接入

## 主链定位

在线企业资料是 QualiBug 的主要资料采集方式；本地文件上传是无法在线读取时的补充方式。两者不会进入两套知识系统，而是统一收敛到：

```text
Connector / Upload
  → Source Occurrence
  → Content Asset / Interpretation Asset
  → Document IR
  → 企业业务理解
  → Campaign / Execution / Evidence
```

## 当前支持

当前正式在线资料 Adapter 均只读访问，并统一进入同一条资料主链：

- 知识空间与节点完整分页发现
- 父子节点递归枚举
- DOC/DOCX 官方 DOCX 导出
- Sheet/Bitable 官方 XLSX 导出
- Drive 文件原始下载
- 显式开启时允许 DOCX 纯文本降级
- `openapi`：在线 OpenAPI/Swagger JSON 或 YAML，支持 ETag、Last-Modified、内容哈希和有界 `$ref` 解析
- `gitee`、`gitlab`、`github`、`git`：使用同一份 Manifest 驱动的 Git 仓库资料适配器，读取分支树、提交变化和显式开启的 Issue/Wiki/Release/Commit 资料

Adapter 不解析业务语义，不创建独立知识库，不写回远端资料系统，也不执行仓库代码、构建脚本或测试脚本。

## 前端入口

正式控制台入口：

```text
/materials?project={project_id}
```

页面按以下顺序呈现：

1. 在线资料源配置、连接测试与立即同步
2. 同步运行状态与遗留运行中止
3. 离线资料补充上传
4. 在线和上传资料的统一 Source Occurrence 清单

连接器范围编辑器由 Manifest 的 `scope_schema` 驱动。对象范围会把字段、默认值、枚举、数组、布尔值和数值约束直接转换为表单；普通用户不需要手工拼接 JSON。保存前会校验 Manifest 声明的必填字段，同时保留旧的 URL/字符串简写兼容性。未知范围字段不会被前端自行补全，仍由连接器配置服务拒绝或接受并返回明确结果。

## 私有服务 API

所有接口均要求登录、租户校验和项目范围校验。配置、测试、同步与中止要求知识资料管理角色：

- `knowledge_admin`
- `project_owner`
- `qa_lead`
- `admin`

### 查询资料源

```http
GET /api/v1/projects/{project_id}/knowledge-connectors
GET /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}
GET /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/runs/{sync_epoch_id}
```

返回值只包含连接状态、加密配置状态、同步状态和来源身份，不返回密钥、访问令牌、原始 cursor 或资料正文。

连接器清单中的 `health` 是 `qualibug.connector-health-projection.v1` 投影，来自既有连接器实例、Profile、自动同步状态、覆盖投影和最新同步收据，不发起额外网络请求，也不创建第二套同步状态。它统一展示授权、同步新鲜度、覆盖比例、失败/重试、资料复用、ACL 传播和语义刷新状态；常见状态包括 `HEALTHY`、`SYNCING`、`STALE`、`NOT_SYNCED`、`REAUTHORIZATION_REQUIRED`、`PERMISSION_INSUFFICIENT`、`AUTHORIZATION_EXPIRING` 和 `DOWNSTREAM_DEGRADED`。没有成功同步收据时，新鲜度保持 `UNKNOWN`、健康状态保持 `NOT_SYNCED`，不能被解释为资料完整或连接成功。

健康投影只返回聚合计数、状态、时间和指纹边界；`source_content_returned`、`credentials_returned`、`raw_cursor_returned` 和 `customer_material_mutation_executed` 必须为 `false`。前端收到不满足这些安全证明的投影会拒绝展示。

### 配置飞书资料源

```http
POST /api/v1/projects/{project_id}/knowledge-connectors
Content-Type: application/json
```

企业自建应用示例：

```json
{
  "connector_instance_id": "feishu-main",
  "display_name": "飞书企业资料",
  "resource_scope": "wiki-all-accessible",
  "status": "ACTIVE",
  "connection_profile": {
    "auth_mode": "internal_app",
    "app_id": "cli_xxx",
    "app_secret": "..."
  }
}
```

支持的资料范围：

```text
wiki-all-accessible
wiki-space:{space_id}
wiki-spaces:{space_id1},{space_id2}
wiki-node:{space_id}:{parent_node_token}
```

更新已有配置时，前端可以用 `********` 表示保留当前加密字段。鉴权方式发生变化时，必须提交新方式所需的真实凭据。

### 配置 Git 仓库资料源

Gitee、GitLab、GitHub 和 generic Git 使用同一套配置形状；`connector_type` 只选择平台 API 适配策略，不创建平行的资料模型：

```json
{
  "connector_instance_id": "gitlab-main",
  "connector_type": "gitlab",
  "display_name": "企业仓库资料",
  "resource_scope": "{\"repository_url\":\"https://gitlab.com/acme/orders\",\"branch\":\"main\",\"include_paths\":[\"docs/**\",\"openapi.yaml\"],\"include_commits\":true}",
  "auth_mode": "personal_access_token",
  "token": "仅提交到加密凭据入口的真实令牌"
}
```

支持的范围字段包括默认/指定分支、包含/排除路径、文件类型、单文件/总字节数、最大文件数，以及是否读取 Issue、Wiki、Release 和 Commit。同步 cursor 绑定仓库、分支 ref、commit SHA、tree hash、平台事件 ID 和范围指纹；普通提交只物化变化文件，重命名、删除、分支删除和 force-push 会产生可追踪生命周期事件或可见覆盖缺口。

### Local Runner 内网访问（OL-009）

内网 GitLab 等资料源必须由客户侧 Local Runner 执行；控制面只签发任务并接收签名结果，不直接访问内网地址，也不持有 Runner 本地凭据。Runner 使用精确 host 白名单、只读适配器和本地加密 profile，任务与结果均为 HMAC 签名。结果进入控制面后仍调用同一个 `sync_connector_snapshot_batch` Source Occurrence 入库主链，不创建第二套资料注册表。

一次性注册和本地初始化可使用随包提供的命令：

```text
qualibug-local-runner register --control-root <control-root> --project <project> \
  --runner-id <runner-id> --allowed-host <gitlab-host> --connector-type gitlab \
  --output bootstrap.json
qualibug-local-runner init --root <runner-root> --bootstrap-file bootstrap.json \
  --profiles-file local-source-profiles.json
```

`local-source-profiles.json` 只在 Runner 端读取并加密保存；不得把 token、密码或数据库 DSN 放进普通配置 API。Runner 断线时保留加密任务和结果 outbox，只有控制面返回同一结果指纹的接受回执后才清理 outbox。`STRUCTURED_ONLY` 模式只上传结构化覆盖观察，不推进内容 cursor，因而不会把“未上传正文”伪装成完整资料接入。

当前产品只将实际具备本地执行契约的 Git 适配器标记为 Local Runner 支持；Confluence、YApi、禅道和数据库在适配器与安全契约完成前保持显式未支持，不生成假资料或假成功。

控制面也提供同一协议的项目范围 API：`POST /api/v1/projects/{project_id}/knowledge-connectors/runners/register` 一次性返回 bootstrap，`GET /api/v1/projects/{project_id}/knowledge-connectors/runners` 只返回 Runner 能力和任务状态，`POST /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/runner/tasks` 签发任务，`POST /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/runner/results` 接收签名结果。所有接口继续复用租户、项目范围和连接器管理角色校验。

### 测试连接

```http
POST /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/test
```

该操作验证 Profile 解密、飞书鉴权和资料范围读取能力，不导入资料。

### 手动同步

```http
POST /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/sync
Content-Type: application/json
```

安全默认请求：

```json
{
  "deletion_policy": "RETAIN",
  "allow_raw_text_fallback": false,
  "max_retire_count": 100,
  "max_retire_ratio": 0.25
}
```

只有完整全量枚举、全部资料处理成功并通过缺失比例/数量门禁时，`RETIRE_MISSING` 才能回收远端已缺失的 occurrence。前端默认使用 `RETAIN`。

同步检查点使用两层治理：

- Sync Registry 保存 SHA-256 指纹，用作 CAS 权威
- Connection Profile Store 加密保存可恢复 checkpoint

服务重启后会先校验两者一致，再发起远端网络请求。失败或部分同步不会推进 checkpoint。

资料物化遵循“只处理变化、最终一次提交”的固定策略：

- 远端 revision 和物化契约未变化时，不重复创建导出任务、不重复下载正文
- 首次接入或多份资料同时变化时，只读导出使用有界并发，结果按远端稳定顺序重新排列
- 任一资料导出失败时，整批不会进入 Source Occurrence 原子入库，既有快照保持不变
- 默认并发为 4，运维可通过 `QUALIBUG_FEISHU_MATERIALIZATION_WORKERS` 调整为 1～8；该参数不暴露给普通用户

### 中止遗留同步

```http
POST /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/abort
Content-Type: application/json

{
  "reason": "operator requested recovery"
}

```

## Generic webhook and event synchronization (OL-018)

Webhook delivery is an optional manifest capability. It is configured once on a connector
instance and does not require a provider-specific event parser. The adapter declares
`webhook_supported`; the instance stores an encrypted `webhook_secret` in the existing
Connection Profile Store and stores only the validated, non-secret `webhook_policy` in the
existing Connector Instance metadata.

```json
{
  "connector_instance_id": "gitlab-main",
  "connector_type": "gitlab",
  "resource_scope": "{\"repository_url\":\"https://gitlab.com/acme/orders\",\"branch\":\"main\"}",
  "connection_profile": {
    "auth_mode": "personal_access_token",
    "token": "submitted only to the encrypted profile authority",
    "webhook_secret": "submitted only to the encrypted profile authority"
  },
  "webhook_policy": {
    "enabled": true,
    "signature_header": "X-Webhook-Signature",
    "event_id_header": "X-Webhook-Event-Id",
    "timestamp_header": "X-Webhook-Timestamp",
    "sequence_header": "X-Webhook-Sequence",
    "signed_payload": "timestamp.body",
    "algorithm": "hmac-sha256",
    "encoding": "hex"
  }
}
```

The callback routes are:

```http
POST /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/webhook
GET  /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/webhook
```

The callback verifies the configured HMAC, timestamp window, event identity, and optional
monotonic sequence before reserving the event in the bounded fingerprint-only ledger. A
duplicate event is acknowledged without another sync; an older sequence is recorded as
out-of-order without a sync; a sequence gap requests calibration. Calibration is cleared
only after the existing managed sync authority returns a complete snapshot. The event
boundary always invokes `run_managed_connector_sync` with `deletion_policy=RETAIN`; it never
directly mutates source material, advances a cursor in the webhook layer, or infers remote
deletion.

Raw event bodies, signatures, plaintext event IDs, source content, credentials, and raw
cursors are not persisted or returned. The ledger retains bounded event fingerprints,
ordering status, sync status, calibration state, and redacted failure details so operators
can distinguish a duplicate, an out-of-order delivery, a lost-event calibration, and a
failed managed sync. A connector with no source-backed event contract remains
`webhook_supported=false`; it must not claim webhook readiness merely because the generic
HTTP route exists.

## Generic OAuth authorization and reauthorization (OL-019)

OAuth is an optional Manifest capability. A connector declares one `oauth_schema` with the
provider authorization endpoint, token endpoint, exact registered redirect URI, public client
id, minimum scopes, and the existing encrypted profile field names for the access token, refresh
token, and optional client secret. No provider endpoint, scope, or credential field is inferred
from a connector type.

The operator starts the flow through the existing authenticated project routes:

```http
POST /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/oauth/start
GET  /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/oauth/callback?code=...&state=...
GET  /api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/oauth
```

The start response contains only a provider authorization URL, transaction identity, requested
scopes, expiry, and PKCE/state safety flags. State is stored only as a hash; the PKCE verifier is
encrypted at rest and single-use. The callback binds the exact transaction, actor, profile
reference, Manifest version, redirect URI, and `S256` challenge before exchanging the code.
Authorization codes, access tokens, refresh tokens, and client secrets never appear in the OAuth
ledger, API projection, logs, or sync receipt. Token values are written only through the existing
encrypted Connection Profile authority.

The callback preserves the existing connector instance, source identity, and encrypted sync
checkpoint. A granted scope that does not contain every Manifest minimum scope is exposed as
`PERMISSION_INSUFFICIENT` and requires reauthorization; it is not treated as a successful sync.
OAuth failure or revocation never triggers source deletion, absence retirement, or cursor loss.
The projection exposes `AUTHORIZED`, `EXPIRING`, `EXPIRED`, `REAUTHORIZATION_REQUIRED`,
`PERMISSION_INSUFFICIENT`, `REVOKED`, `NOT_AUTHORIZED`, or `NOT_SUPPORTED`, together with
required/granted scopes and a bounded failure reason.

## Generic connector acceptance (OL-012)

All registered connector types use the same acceptance contract and the same managed sync
authority. The compatibility command remains available for Feishu, while new integrations use:

```text
qualibug-connector-tenant-acceptance --project <project_id> --connector <connector_id>
```

The contract requires an available connection, an explicit read-only network observation,
non-persisted credentials, balanced discovery accounting, zero unknown gaps and failures,
recoverable checkpoint evidence, bounded duration, and repeat-sync reuse. A missing safety or
completeness observation fails closed. Reports contain fingerprints rather than raw cursors,
receipt paths, credentials, or source content.

The API uses the existing routes under
`/api/v1/projects/{project_id}/knowledge-connectors/{connector_id}/acceptance`,
`acceptance-reports`, and `acceptance-jobs`. Feishu keeps its legacy schema; all other registered
connectors use the generic connector acceptance schemas. Unsupported source capabilities remain
explicit coverage gaps and cannot be converted into an acceptance pass.

## ACL Visibility and Incremental Semantic Refresh (OL-010/OL-011)

Each sync records source-bound remote ACL evidence in the existing Connector Sync Registry:

- Only ACL version, visibility, inheritance, capture time, and project/connector-scoped fingerprints are retained. Raw users, groups, and remote principal IDs are never persisted or returned to ordinary frontend projections.
- Missing, incomplete, or unknown ACL availability is fail-closed as `BLOCKED_INCOMPLETE`. Permission denial, remote deletion, and remote unavailability remain distinct auditable states; absence is never inferred as deletion.
- A local project share is an explicit action by an authorized manager and cannot bypass missing or incomplete remote ACL evidence. Historical bytes remain retained while current user visibility is re-evaluated at the projection boundary.
- The asset, Command Center, preview, and connector-resource routes use the same visibility decision. Denied Source Occurrences, content blocks, and nested derived rows are not returned.

After sync, `qualibug.connector-semantic-refresh.v1` records the bounded impact handoff:

```text
Connector Sync Complete
  -> Source Occurrence Diff
  -> Artifact Diff
  -> affected ContentBlock
  -> fact / entity / behavior impact
  -> scenario / regression impact
```

The incremental semantic executor is now installed on the existing knowledge composition root. An initial
connector sync performs the explicit baseline build; later `SOURCE_REVISION_CHANGED`, `SOURCE_CREATED`,
`SOURCE_REAPPEARED`, and `SOURCE_CAPABILITY_NOW_SUPPORTED` events parse and semantically analyze only the
changed Source Occurrences. The existing deterministic technical, fact, conflict, behavior, scenario, and
Probe authorities then reconcile the final asset. Unchanged materials are not re-read or sent through LLM
analysis. Shared API artifact records are source-scoped, so a changed declaration cannot discard an unchanged
declaration from another Source Occurrence.

`SOURCE_BECAME_UNAVAILABLE`, `SOURCE_PERMISSION_CHANGED`, and `SOURCE_RETIRED` keep the related facts,
behaviors, scenarios, and regression probes visible but mark them `PENDING_SOURCE_VALIDATION`/invalidated;
they are never silently deleted or executed. Each completed handoff persists an inspectable
`qualibug.connector-semantic-impact.v1` relation ledger and stage counts. Ordinary frontend projections expose
only event types, source-label fingerprints, impact counts, relation counts, and stage status—not raw remote
identities or content.

## OpenAPI export reuse (OL-014)

Apifox and YApi exports use the same read-only OpenAPI adapter as `openapi`. Configure
`connector_type` as `apifox` or `yapi` with the exported JSON/YAML URL. The selected type
only preserves the source label; it does not create a second semantic parser or change the
SSRF checks, size limits, conditional requests, bounded `$ref` traversal, or incremental
fingerprint rules. Platform-private catalogs and non-OpenAPI exports remain explicit coverage
gaps until a source-backed adapter contract exists.

中止操作不推进 cursor，不删除现有资料快照，只清理遗留运行状态和对应租约。

## 凭据和并发治理

- Connector Instance 只保存 `vault-ref://connectors/{id}`
- Profile Store 中的 App ID、App Secret、Token 和 checkpoint 均加密落盘
- 前端永不回显密钥明文
- Profile 配置和 checkpoint 提交复用项目级 Enterprise Knowledge Transaction Lease
- 同一 Connector Instance 的同步批次使用独占文件系统租约
- 在线访问由每个 Connector Manifest 和用户声明的 `resource_scope` 决定；不得在通用路径中硬编码厂商域名，所有远端 URL 都必须经过 SSRF 校验
- 在线 OpenAPI/Swagger JSON 或 YAML 通过 `connector_type: "openapi"` 接入，使用只读 GET、ETag/Last-Modified/内容哈希和有界 `$ref` 解析，并复用同一 Source Occurrence 主链
- 所有重定向继续经过 SSRF 校验

## 离线补充

无法在线读取的 PRD、接口文档、历史缺陷、数据库说明、测试资料和设计稿可在同一 `/materials` 页面上传。上传资料继续进入同一 Source Occurrence 权威，不建立第二套解析、存储或知识链。
