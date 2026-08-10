# QualiBug 前端企业资料就绪阻塞 SPEC

状态：已实现  
范围：仅前端企业资料状态解释、资料类型展示、优先级和下一步导航；不定义或修改后端 Connector、知识解析、业务理解、扫描、Finding、Oracle 或 Release 判定。

## 1. 目标

企业资料首页需要同时回答两个不同问题：

1. **资料现在处于什么真实状态？**
2. **客户现在最应该先处理什么？**

前端保留三层事实：

`在线来源已连接 → 资料已同步 → 业务理解输入`

并额外突出一个“当前最大阻塞 / 唯一主 CTA”。三层状态用于解释事实；最大阻塞用于决定下一步，两者不能混成一套状态。

## 2. Online-first，类型 Open-ended

企业资料以在线资料源为主、文件上传为补充，但**资料类型不得被前端固定白名单限制**。

任何后端真实识别、成功 materialize 且状态为 `active` 的 source，都可以进入前端“业务理解输入主链”展示。

这包括但不限于：

- PRD / 需求；
- API / 接口；
- DB / 数据设计；
- UI / UX 设计文档；
- 原型 / 交互稿；
- 协作文档 / 知识库；
- 历史 Bug；
- 测试方案 / 测试用例；
- 架构文档；
- 权限说明；
- 业务流程；
- 数据字典；
- 用户手册；
- 部署 / 发布资料；
- 后端未来新增并真实返回的其他 `source_type`。

上面的类型只是示例，不是允许名单。

前端可以维护常见 `source_type -> 中文展示名` 的别名字典，但该字典**只能用于显示友好名称**。当后端返回未知 `source_type` 时，前端必须原样展示，不能过滤、丢弃或判定为“非核心资料”。

## 3. 状态权威

前端只消费后端已有事实：

- `listKnowledgeConnectors(project)`：Connector 实例、health、OAuth、auto_sync、coverage、下游刷新状态；
- `getKnowledgeAsset(project)`：真实 materialized source、source status、source type、online/upload 来源身份。

禁止：

- 从错误文本猜授权失败；
- 从 Connector 存在推断同步完成；
- 从资料同步完成推断业务理解正确；
- 从某个固定资料类型缺失推断“业务理解不完整”；
- 从当前没有数据推断“没有资料”，当对应状态接口实际读取失败时。

## 4. 三层资料就绪状态

### 4.1 在线来源已连接

权威来源：`listKnowledgeConnectors(project)`。

含义仅为：后端存在真实在线 Connector 实例。

Connector inventory 读取失败时显示“状态待核对”，不得显示“未连接”。

### 4.2 资料已同步

权威来源：`getKnowledgeAsset(project)` 中真实 materialized source。

在线资料身份来自：

- `source_origin === ONLINE_CONNECTOR`；或
- 兼容 `source_ref` 以 `connector://` 开头。

只有 `active source` 才进入可用资料统计。Connector 已连接但没有真实 active source 时仍然是“等待首次读取”。

### 4.3 业务理解输入

该阶段只表达：

**真实资料是否已经进入 QualiBug 的输入主链。**

规则：

- 至少一份真实 `active source` → 输入主链已建立；
- 没有真实 active source → 等待可读资料；
- material inventory 读取失败 → 无法确认。

前端不得再使用类似：

`BUSINESS_CONTEXT_TYPES = { PRD, API, DB, ... }`

或：

`businessContextActive === 0 -> 阻塞`

这样的固定类型判定。

任何真实 active source 都允许进入输入主链；至于不同资料对后端业务理解、测试规划和 Bug 发现实际贡献多大，由后端对应能力负责，不由前端类型白名单决定。

## 5. 动态资料类型分布

企业资料首页展示“资料类型分布”，完全由真实 active source 的 `source_type` 动态生成。

规则：

1. 遍历所有真实 `active source`；
2. 按 `source_type` 动态计数；
3. 没有 `source_type` 的资料归为“未分类资料”；
4. 常见类型可以使用中文展示别名；
5. 未知类型必须原样展示；
6. 新的后端类型不需要修改前端白名单即可出现。

正确示例：

`已观察到 8 类 active 资料`

下面可以出现：

`PRD / 需求 3 份`、`UI / UX 设计 5 份`、`architecture_spec 2 份`、`custom_domain_rule 1 份`。

其中 `architecture_spec`、`custom_domain_rule` 即使前端没有预置中文别名，也必须正常显示。

### 5.1 禁止固定分母

不得再展示：

- `3/5 类核心输入`；
- `60% 资料完成率`；
- `核心五类资料齐全率`。

因为企业资料类型不是一个固定全集。

`observedTypeCount` 只能表示当前真实观察到多少种类型，没有固定 denominator。

### 5.2 类型分布不是质量指标

资料类型数量不得解释成：

- 业务理解准确率；
- 业务理解完成率；
- 资料完整率；
- Bug 发现能力评分；
- 运行门禁。

例如只有 UI/UX、原型和协作文档，也不能因为没有 PRD/API/DB 就由前端判定“不具备业务理解输入”。

最终能否执行扫描仍由 Run Center 后端 preflight 判定。

## 6. Connector 前端注意状态

### 6.1 授权 / 权限

以下真实状态进入授权注意集合：

- `REAUTHORIZATION_REQUIRED`；
- `PERMISSION_INSUFFICIENT`；
- `AUTHORIZATION_EXPIRING`；
- `health.reauthorization_required === true`；
- `connection_profile.reauthorization_required === true`。

### 6.2 暂停 / 关闭

Connector 或 health 为 `PAUSED / DISABLED` 时，表示持续更新中断。

### 6.3 同步失败

以下 health 状态视为同步失败：

- `DEGRADED`；
- `CALIBRATION_REQUIRED`。

material source 的 `failed / degraded` 独立计入资料处理失败。

### 6.4 下游刷新未完成

`health.status === DOWNSTREAM_DEGRADED` 只表达：

**资料已读取，但下游业务理解/语义刷新尚未完成。**

不得改写成“理解错误”或“理解失败”。

### 6.5 同步 / 重试进行中

包括：

- `active_sync_epoch_id`；
- `auto_sync.state === running / retrying`；
- `health.status === SYNCING / RETRYING`；
- material `processing`。

### 6.6 部分资料未覆盖

包括：

- `health.status === PARTIAL_COVERAGE`；
- `coverage.status === PARTIAL_UNSUPPORTED`；
- `coverage.unsupported_count > 0`。

部分未覆盖不能否定已经读取成功的资料，但不能包装成完整覆盖。

## 7. 最大阻塞优先级

`deriveCurrentBlocker()` 按以下顺序选择第一个命中的状态：

1. 状态读取失败；
2. 授权 / 权限需要处理；
3. 资料源暂停或关闭；
4. Connector 同步失败 / material failed-degraded；
5. 下游业务理解刷新未完成；
6. 同步 / 重试 / processing；
7. 部分在线资源未支持；
8. 没有任何真实资料，也没有在线 Connector；
9. Connector 已连接，但还没有任何真实 active source；
10. 当前只有文件补充资料；
11. 已有真实 active source → 资料输入主链已建立。

**不得再出现“固定资料类型不足”这一阻塞级别。**

资料类型数量、某个具体类型是否存在，都不能进入 `deriveCurrentBlocker()` 的 Ready/Block 判定。

## 8. 唯一主 CTA

企业资料摘要只突出一个当前最高优先级动作：

- 状态读取失败 → `重新核对资料状态`；
- 授权 / 权限 → `处理资料源授权`；
- 暂停 / 关闭 → `查看资料源状态`；
- 同步失败 → `查看同步异常`；
- material failed/degraded → `查看异常资料`；
- DOWNSTREAM_DEGRADED → `查看刷新状态`；
- 同步 / processing → `重新核对最新状态`；
- partial unsupported → `查看未覆盖资料`；
- 没有来源 → `连接在线资料`；
- 已连接但未 materialize → `重新核对首次同步`；
- 只有文件补充 → `连接在线资料（推荐）`；
- 已有真实 active source → `下一步：系统与环境`。

Online-first 是推荐策略，不是人为新增的执行门禁。

## 9. CI 合同

`test:settings-onboarding` 必须锁定：

- Connector attention 来自 typed Connector health / OAuth / coverage；
- material 状态来自真实 source；
- active source 类型动态计数；
- 未知 `source_type` 原样展示；
- `ui_ux` 等常见类型可以只作为友好别名；
- 不存在固定 `BUSINESS_CONTEXT_TYPES` 白名单；
- 不存在固定五类 `BusinessInputCounts`；
- 不存在 `/5` 类核心资料分母；
- 类型数量不得进入 Ready/Block 判断；
- 授权、同步、coverage 等真实阻塞优先级保持不变；
- 读取失败 fail-closed；
- 只有一个当前最重要 CTA；
- Run Center preflight 仍是最终执行权威。

## 10. 非目标

本 SPEC 不定义：

- 哪些资料“最重要”；
- 企业必须提供哪些固定资料类型；
- Connector 如何同步；
- 资料如何解析；
- 业务理解算法；
- 大模型推理；
- Bug 发现算法；
- 执行 / Oracle / Evidence / Regression；
- Release Gate。

前端只负责把后端已经存在的资料事实真实、开放地展示出来，不把自身展示分类演变成产品能力边界。
