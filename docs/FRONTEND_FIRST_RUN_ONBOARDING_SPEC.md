# QualiBug 前端首次接入单主线 SPEC

状态：已实现  
范围：仅前端信息架构、状态展示、导航和交互；不定义或修改任何后端 Bug 发现、扫描、Oracle、Finding、Coverage、Release Gate 或 Regression Gate 逻辑。

## 1. 目标

首次客户从创建/选择项目开始，前端必须始终给出一个明确、可执行、不会绕过真实后端门禁的下一步。

主闭环：

`选择/创建客户 → 系统与环境 + 企业资料 → 运行前检查 → 标准扫描 → 价值总览 → 问题/证据 → 发布门禁`

“系统与环境”和“企业资料”都是首次扫描的前置能力，允许客户按实际情况先完成任一项；前端必须在完成一项后主动把客户交接到另一项，而不是留下死路。

## 2. 企业资料：Online-first，Upload-second

企业资料的唯一正式入口是：

`/materials?project=<project>`

产品默认策略：

1. **优先连接企业在线资料源**：在线文档、知识库或其他后端 Manifest 已声明支持的在线来源；
2. **持续读取和更新**：连接器负责授权、范围、同步、健康状态、新鲜度和自动恢复；
3. **文件上传只作补充**：用于在线来源没有覆盖的 PRD、接口文档、历史缺陷、数据库说明、设计稿等；
4. **前端不得假装支持某个具体平台**：支持列表必须来自后端真实 Connector Manifest。

资料页面的信息架构必须保持：

`在线连接器 / 在线资料 → 文件补充上传 → 统一已接入资料`

文件上传区域必须明确标记为“补充方式”，不得重新成为首次接入主 CTA。

Settings 不再维护第二套文件上传和知识入库 UI，只保留：

- 客户项目选择/创建；
- 企业资料状态摘要；
- “连接企业资料”导航。

禁止在 Settings 重新引入 `type="file"`、`ingestKnowledgeFiles` 或另一套资料解析/上传流程。

## 3. 资料完成条件与非阻塞原则

Online-first 是**产品推荐路径**，不是伪造新的执行门禁。

首次资料条件仍以真实知识资产是否存在可读资料为准：

- 至少一份真实 active 在线资料：资料条件完成，推荐继续系统与环境；
- 只有真实 active 文件补充资料：资料条件也可以完成，不阻塞首次运行；但 UI 必须明确建议继续连接在线资料源，以减少人工维护和资料过期；
- 在线 Connector 已经连接但首次同步尚未形成真实可读 source：显示“在线来源已连接 / 等待首次读取”，但**不得提前标记资料步骤完成**；
- processing：不得视为 clean ready；
- failed/degraded：不得视为 clean ready；
- 状态读取失败：不得解释成“没有资料”。

前端不得为了强调 Online-first 而凭空阻塞一个后端已经允许执行的项目，也不得因为“连接器已经存在”就把尚未 materialize 的资料提前当成可读资料。

## 4. Materials 三层资料就绪链路

`MaterialsOnboardingHandoff` 是企业资料首页的真实性摘要，必须明确展示三个彼此独立的阶段：

`在线来源已连接 → 资料已同步 → 业务理解输入`

这三个阶段不能互相替代。

### 4.1 在线来源已连接

权威来源：`listKnowledgeConnectors(project)`。

含义仅为：已经存在后端真实在线 Connector 实例。

禁止：

- 因 Connector 存在就显示“资料已同步”；
- 因 Connector 存在就标记首次资料条件完成；
- Connector inventory 读取失败时显示“未连接”。

如果 Connector inventory 读取失败，应显示“状态待核对”，并保留已经从知识资产读取到的其他真实状态。

### 4.2 资料已同步

权威来源：`getKnowledgeAsset(project)` 中真实 materialized source。

在线资料必须满足真实 active source，并通过以下来源身份识别：

- `source_origin === ONLINE_CONNECTOR`；或
- 兼容 `source_ref` 以 `connector://` 开头。

状态规则：

- Connector 已连接但没有 active 在线 source → `等待首次读取`；
- 至少一份 active 在线 source → `N 份在线资料可用`；
- 只有 active 上传文件 → `当前仅文件补充`，可以继续但推荐连接在线来源；
- material 状态读取失败 → `状态待核对`，不得推导同步成功或失败。

### 4.3 业务理解输入

该阶段只能表达**输入资料是否已经进入 QualiBug 业务理解输入主链**，不得表达“QualiBug 已经理解正确”“理解已完成”或任何理解准确率。

前端展示层当前把以下真实 active source type 视为核心业务理解输入：

- `prd`；
- `openapi`；
- `database_schema` / `db_design`；
- `collaboration_document`；
- `historical_bug`。

这是**前端就绪表达白名单**，不是后端业务理解算法，也不是新的执行门禁。

状态规则：

- 至少一份上述核心 active source → `N 份核心输入已就绪`；
- 有其他 active source、但没有上述核心类型 → `输入主链已建立 / 核心输入待补齐`；
- 没有真实 active source → `等待可读资料`；
- material 状态不可读 → `无法确认`。

所有文案必须明确：

**“输入可用”不等于“业务理解正确或完整”。**

## 5. Materials → 系统与环境交接

`MaterialsOnboardingHandoff` 只在 `/materials` 生效，并同时读取：

- `getKnowledgeAsset(project)`；
- `listKnowledgeConnectors(project)`。

两者使用 `Promise.allSettled` 保留部分真实状态：一项读取失败不得抹掉另一项已经成功读取到的事实。

它必须区分：

- `onlineActive`：来自在线连接器的 active source；
- `uploadedActive`：非在线来源的 active 文件补充资料；
- `businessContextActive`：进入业务理解输入展示白名单的 active source；
- `connectorCount`：真实在线 Connector 数量。

状态规则：

- 无资料、无连接器：主动作连接在线资料；
- 有 Connector、无可读在线资料：显示“已连接 / 等待首次读取”；
- processing：提示仍在处理，可以同时配置系统与环境；
- failed/degraded：显示异常并允许重新核对；
- 至少一份在线 active 且 clean：显示在线资料已同步，进入 `/settings`；
- 只有上传文件且 clean：显示“文件补充已可用”，提供“连接在线资料（推荐）”，同时保留“暂用补充资料继续”的非阻塞路径；
- 已经配置过系统的客户可直接进入 `/campaigns`，但是否能运行仍由真实 preflight 决定。

资料状态每 5 秒重新核对，避免同步/上传完成后客户仍长时间看到旧状态。

## 6. Settings 接入向导

Settings 必需步骤只有三项：

1. 系统地址；
2. 测试账号；
3. 企业资料。

数据库是可选增强，不阻塞首次体验。

向导分别从真实接口读取：

- `listConnectors(project)`：被测系统连接状态；
- `getServiceCredentials(project)`：测试账号与数据库凭据状态；
- `getKnowledgeAsset(project)`：已经 materialize 的真实企业资料；
- `listKnowledgeConnectors(project)`：已经配置的企业在线资料连接器。

企业资料状态必须区分：

- 已连接在线资料源数量；
- 已形成的在线资料数量；
- 文件补充数量；
- 资料总数。

只有三个必需状态都成功读取且均完成时，前端才显示“基础接入已完成”。资料完成仍按“至少一个真实可读资料 source 存在”判定；连接器已存在但尚未产生可读 source 时只能显示同步中间态，不得提前 Ready。仅有文件补充时可以 Ready，但必须提示在线资料是推荐主来源。

主 CTA 按缺失状态变化：

- 缺系统地址 → 接入系统；
- 缺测试账号 → 补充测试账号；
- 缺企业资料且没有在线连接器 → **连接企业在线资料**；
- 在线连接器已存在但尚无可读资料 → **查看在线资料同步**；
- 三项完成 → 继续运行前检查；
- 任一状态读取失败 → 先重新核对，不把读取失败当成配置缺失。

## 7. 运行前检查是最终执行权威

前端 onboarding 完成不等于扫描一定可运行。

进入 `/campaigns` 后必须继续满足真实 `preflight.ready === true`；否则：

- 主扫描按钮 disabled；
- handler fail-closed；
- 显示真实 blocker；
- 引导回 Settings 补充必要条件。

任何 onboarding UI 都不得为了缩短流程绕过这一门禁，也不得因为“只有文件补充、没有在线来源”自行创造一个后端不存在的新门禁。

三层资料就绪链路只用于客户理解资料接入状态，不得覆盖运行中心 preflight。

## 8. 首次使用四步引导

Dashboard `JourneyStrip` 使用真实流程措辞：

1. 接入被测系统；
2. **连接企业资料**；
3. 运行前检查并检测；
4. 查看结果与发布建议。

第 2 步必须明确：

`优先连接企业在线文档或知识库持续同步，缺失资料再用文件上传补充`

动作进入 `/materials`，使用“连接资料源”而非“上传资料”作为主 CTA。

第 4 步必须进入 `/dashboard`，不能直接假设一定存在 Finding。问题清单和证据中心属于结果后的条件分支。

## 9. Materials 页面顺序合同

Materials 页面已有在线连接器能力时，前端不得重新把上传区提升到其前面。

稳定顺序：

1. 页面总览与三层资料就绪状态；
2. 在线连接器 / 在线资料连接、授权、范围、同步与健康状态；
3. 文件补充上传；
4. 在线与文件统一后的资料清单。

文件补充上传文案必须说明其用途是“补充在线资料没有的内容”。

## 10. 全局导航层级

侧边栏主流程：

`系统总览 → 运行中心 → 问题清单 → 证据中心 → 发布门禁`

项目接入：

`企业资料 → 系统与环境`

高级视图：

`覆盖矩阵 → 后台任务`

发布门禁属于客户主流程，不得重新降级到“高级视图”。

## 11. 全局状态真实性

Sidebar 必须区分：

- 未选择客户；
- 检测进行中；
- 已确认 P0；
- 已确认普通缺陷；
- 后台补证中；
- 已有真实扫描结果但当前无已确认问题；
- 从未形成扫描结果。

因此，0 Finding 的已完成扫描不得再显示成“等待首次验证”。

## 12. 回归契约

`test:settings-onboarding` 已进入 `npm run ci:gate`，至少锁定：

- 三个必需 onboarding 状态来自真实接口；
- 企业资料必须区分“已连接在线来源”和“已形成真实可读资料”；
- 在线 Connector 存在但尚未 materialize 时不得提前 Ready；
- Materials 首页必须展示“在线来源已连接 / 资料已同步 / 业务理解输入”三层状态；
- Connector inventory 与 material inventory 读取失败必须独立表达；
- 业务理解输入必须来自真实 active source，不能从 Connector 存在推导；
- “核心输入已就绪”只表示输入资料可用，不得宣称理解正确、理解完整或给出理解准确率；
- 企业资料必须区分在线来源和文件补充；
- 缺资料主 CTA 必须是“连接企业在线资料”；
- 已连接但未 materialize 的在线来源必须进入“查看在线资料同步”；
- 只有文件补充时不得阻塞首次运行，但必须推荐在线来源；
- Settings 完成态必须有可点击的“继续运行前检查”；
- 状态读取失败必须先重新核对；
- Materials 与 Settings 之间保留 project 上下文；
- Settings 不得重新出现第二套资料上传；
- Materials handoff 不得把 processing/failed 当 clean ready；
- Materials 在线连接器必须排在文件上传之前；
- 文件上传必须明确属于“补充方式”；
- JourneyStrip 必须使用“连接企业资料 / 连接资料源”语义；
- JourneyStrip 必须先经过运行前检查，结果步骤必须回 Dashboard；
- Release Gate 必须属于主流程；
- clean scan 不得在 Sidebar 显示“等待首次验证”；
- Run Center 仍由真实 preflight fail-closed。

## 13. 非目标

本 SPEC 不涉及：

- Bug 发现率；
- 业务理解算法；
- 业务理解正确率；
- 业务理解完整度判定；
- 场景生成；
- Oracle / Observer / Experiment；
- Finding 成立条件；
- Coverage 计算；
- Release / Regression Gate 后端判定；
- 扫描执行状态机；
- 具体第三方在线资料平台是否被后端支持。

上述能力由后端其他模块/Agent 负责，前端只负责准确消费和表达。
