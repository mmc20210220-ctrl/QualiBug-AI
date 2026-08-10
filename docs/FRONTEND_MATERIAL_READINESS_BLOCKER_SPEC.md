# QualiBug 前端企业资料就绪阻塞 SPEC

状态：已实现  
范围：仅前端企业资料状态解释、优先级和下一步导航；不定义或修改后端 Connector、知识解析、业务理解、扫描、Finding、Oracle 或 Release 判定。

## 1. 目标

企业资料首页不能只展示多个 Ready / Not Ready 状态，还必须回答客户一个问题：

**“现在最应该先处理什么？”**

前端在保留三层事实的同时，只突出一个当前最高优先级动作：

`在线来源已连接 → 资料已同步 → 业务理解输入`

三层状态用于解释事实；“当前最大阻塞”用于决定唯一主 CTA。两者不能混为一套状态。

## 2. 状态权威

前端只消费后端已有事实：

- `listKnowledgeConnectors(project)`：Connector 实例、health、OAuth、auto_sync、coverage、下游刷新状态；
- `getKnowledgeAsset(project)`：真实 materialized source、source status、source type、online/upload 来源身份。

禁止：

- 从错误文本猜授权失败；
- 从 Connector 存在推断同步完成；
- 从资料同步完成推断业务理解正确；
- 从当前没有数据推断“没有资料”，当对应状态接口实际读取失败时。

## 3. Connector 前端注意状态

### 3.1 授权 / 权限

以下真实状态进入授权注意集合：

- `REAUTHORIZATION_REQUIRED`；
- `PERMISSION_INSUFFICIENT`；
- `AUTHORIZATION_EXPIRING`；
- `health.reauthorization_required === true`；
- `connection_profile.reauthorization_required === true`。

OAuth 和 Connector health 都可提供该事实；前端按 Connector 去重计数。

### 3.2 暂停 / 关闭

Connector 或 health 为 `PAUSED / DISABLED` 时，视为持续更新中断，需要客户确认是否恢复。

### 3.3 同步失败

以下 health 状态视为同步失败：

- `DEGRADED`；
- `CALIBRATION_REQUIRED`。

同时，material source 的 `failed / degraded` 必须独立计入资料处理失败。

### 3.4 下游刷新未完成

`health.status === DOWNSTREAM_DEGRADED` 表示资料已读取但下游语义/业务理解刷新没有完成。

前端只能表达：

**“资料已读取，但业务理解刷新尚未完成。”**

不得表达“理解失败”“理解错误”或自行推导后端理解质量。

### 3.5 同步 / 重试进行中

以下任一真实事实表示 Connector 仍在工作：

- `active_sync_epoch_id` 存在；
- `auto_sync.state === running / retrying`；
- `health.status === SYNCING / RETRYING`。

material source 的 `processing` 与 Connector 同步中并列展示。

### 3.6 部分资料未覆盖

以下任一事实表示在线来源部分资源未被支持：

- `health.status === PARTIAL_COVERAGE`；
- `coverage.status === PARTIAL_UNSUPPORTED`；
- `coverage.unsupported_count > 0`。

此状态不否定已经读取到的资料，但不得把部分同步包装成完整覆盖。

## 4. 最大阻塞优先级

`deriveCurrentBlocker()` 必须按以下顺序从上到下选择第一个命中的状态：

1. **状态读取失败**：material 或 Connector inventory 无法核对；
2. **授权 / 权限需要处理**；
3. **资料源暂停或关闭**；
4. **Connector 同步失败 / material failed-degraded**；
5. **下游业务理解刷新未完成**；
6. **同步 / 重试 / 资料处理进行中**；
7. **部分在线资源未支持**；
8. **尚未连接在线资料且也没有文件补充**；
9. **在线来源已连接但尚未形成任何可读资料**；
10. **当前只有文件补充资料**；
11. **已有可读资料但核心业务理解输入不足**；
12. **资料输入主链已就绪**。

高优先级状态不得被低优先级“建议”覆盖。例如：

- 授权失效时不得先提示“补充 PRD”；
- 同步失败时不得先提示“连接更多资料”；
- DOWNSTREAM_DEGRADED 时不得显示“业务理解输入已完全就绪”；
- 只有文件补充时可以继续运行，但仍可把“连接在线资料”作为产品推荐动作。

## 5. 唯一主 CTA

企业资料就绪摘要只展示一个当前最高优先级主动作：

- 状态读取失败 → `重新核对资料状态`；
- 授权 / 权限问题 → `处理资料源授权`；
- 暂停 / 关闭 → `查看资料源状态`；
- 同步失败 → `查看同步异常`；
- material failed/degraded → `查看异常资料`；
- DOWNSTREAM_DEGRADED → `查看刷新状态`；
- 同步 / processing → `重新核对最新状态`；
- partial unsupported → `查看未覆盖资料`；
- 未连接在线来源 → `连接在线资料`；
- 已连接但未 materialize → `重新核对首次同步`；
- 仅文件补充 → `连接在线资料（推荐）`；
- 核心输入不足 → `检查资料范围`；
- 已就绪 → `下一步：系统与环境`。

Connector 类动作滚动到真实在线连接器区域；material failure 滚动到统一资料清单；就绪后进入 `/settings?project=<project>`。

不得为上述 CTA 新造后端写接口。

## 6. Online-first 非阻塞原则

Online-first 是产品路径优先级，不是新的执行门禁。

只有文件补充资料时：

- 真实 active 文件仍然是可用资料；
- 前端不得人为阻断 Run Center；
- 当前推荐动作可以是“连接在线资料（推荐）”；
- 最终能否运行仍由 `/campaigns` 的真实 backend preflight 决定。

## 7. 业务理解表达边界

`businessContextActive > 0` 只表示以下真实 active source 已进入输入主链：

- PRD；
- OpenAPI；
- Database Schema / DB Design；
- Collaboration Document；
- Historical Bug。

前端不得基于此数字宣称：

- “业务理解完成”；
- “理解准确率达到 X%”；
- “业务规则已经完整”；
- “扫描一定可以发现对应 Bug”。

正确表达是：

**“核心业务理解输入已形成真实可读 source。”**

### 7.1 核心输入覆盖面板

企业资料首页必须进一步把核心输入拆成五个客户可理解的类别：

1. `PRD / 需求` → `source_type === prd`；
2. `API / 接口` → `source_type === openapi`；
3. `DB / 数据结构` → `source_type === database_schema | db_design`；
4. `协作文档` → `source_type === collaboration_document`；
5. `历史 Bug` → `source_type === historical_bug`。

每一类只统计真实 `active source`。同一类别有多份资料时展示真实数量，例如 `✓ 3 份`。

没有观察到某一类时必须写成：

**“未观察到”**

而不是：

- “缺失”；
- “未完成”；
- “必须补充”；
- “理解失败”。

原因是并非所有企业项目都必须同时具备 DB、历史 Bug 或其他五类资料。

面板顶部可以显示：

`已观察到 N/5 类核心输入`

但这里的 `N/5` 只表示**输入类型分布**，不得解释成：

- 完成率；
- 资料齐全率；
- 业务理解准确率；
- 业务理解完成率；
- 扫描能力得分；
- 新的前端运行门禁。

尤其禁止使用 `businessInputCategoryCount < 5` 阻止客户进入运行前检查。真正的执行权威仍是 Run Center 后端 preflight。

如果 material inventory 读取失败，五个类别全部显示“无法确认”，不得把空值变成五个“未观察到”。

## 8. CI 合同

`test:settings-onboarding` 必须锁定：

- Connector attention 来自 typed Connector health / OAuth / coverage 字段；
- material 状态来自真实 source；
- 最大阻塞判断顺序不可倒置；
- 授权问题必须高于同步、coverage、核心输入建议；
- DOWNSTREAM_DEGRADED 独立表达；
- PARTIAL_UNSUPPORTED 独立表达；
- 读取失败 fail-closed；
- 只有一个“当前最重要动作”主 CTA；
- 在线来源动作进入在线连接器区域；
- material failure 动作进入真实资料清单；
- 已就绪动作进入 Settings；
- 五类核心输入必须来自真实 active source；
- DB 类必须统一计算 `database_schema / db_design`；
- 未观察到某类输入不得变成必填失败；
- `N/5` 只能表达已观察输入类别，禁止叫完成率或理解准确率；
- 五类覆盖不得成为新的前端运行门禁；
- Run Center preflight 权威保持不变。

## 9. 非目标

本 SPEC 不定义：

- Connector 如何同步；
- OAuth 如何签发或刷新；
- 资料如何解析；
- 业务理解算法；
- 大模型推理；
- Bug 发现算法；
- 执行 / Oracle / Evidence / Regression；
- Release Gate。

前端只负责把后端已经存在的资料事实，以正确优先级变成客户能立即理解并执行的下一步。
