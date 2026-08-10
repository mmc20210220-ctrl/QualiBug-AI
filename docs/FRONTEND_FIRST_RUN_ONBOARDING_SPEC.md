# QualiBug 前端首次接入单主线 SPEC

状态：已实现  
范围：仅前端信息架构、状态展示、导航和交互；不定义或修改任何后端 Bug 发现、扫描、Oracle、Finding、Coverage、Release Gate 或 Regression Gate 逻辑。

## 1. 目标

首次客户从创建/选择项目开始，前端必须始终给出一个明确、可执行、不会绕过真实后端门禁的下一步。

主闭环：

`选择/创建客户 → 系统与环境 + 企业资料 → 运行前检查 → 标准扫描 → 价值总览 → 问题/证据 → 发布门禁`

“系统与环境”和“企业资料”都是首次扫描的前置能力，允许客户按实际情况先完成任一项；前端必须在完成一项后主动把客户交接到另一项，而不是留下死路。

## 2. 企业资料：Online-first，Upload-second，类型开放

企业资料的唯一正式入口是：

`/materials?project=<project>`

产品默认策略：

1. **优先连接企业在线资料源**：在线文档、知识库或其他后端 Manifest 已声明支持的在线来源；
2. **持续读取和更新**：连接器负责授权、范围、同步、健康状态、新鲜度和自动恢复；
3. **文件上传只作补充**：用于在线来源没有覆盖的需求、接口、UI/UX、原型、测试、架构、历史缺陷、数据库说明或其他企业资料；
4. **前端不得假装支持某个具体平台**：支持列表必须来自后端真实 Connector Manifest；
5. **前端不得限定固定资料类型全集**：后端真实返回的新 `source_type` 必须可以直接进入资料展示和输入主链。

资料页面的信息架构保持：

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

资料完成条件**不得**依赖某个固定 `source_type` 清单，也不得因为没有 PRD、API、DB、历史 Bug 或任何其他具体类型就由前端自行阻塞。

最终能否执行仍由 Run Center 后端 preflight 判定。

## 4. Materials 三层资料就绪链路

`MaterialsOnboardingHandoff` 是企业资料首页的真实性摘要，必须明确展示三个彼此独立的阶段：

`在线来源已连接 → 资料已同步 → 业务理解输入`

### 4.1 在线来源已连接

权威来源：`listKnowledgeConnectors(project)`。

只表示已经存在后端真实在线 Connector 实例。

禁止：

- 因 Connector 存在就显示“资料已同步”；
- 因 Connector 存在就标记首次资料条件完成；
- Connector inventory 读取失败时显示“未连接”。

Connector inventory 读取失败时显示“状态待核对”，并保留知识资产侧已经成功读取到的其他真实状态。

### 4.2 资料已同步

权威来源：`getKnowledgeAsset(project)` 中真实 materialized source。

在线资料身份通过：

- `source_origin === ONLINE_CONNECTOR`；或
- 兼容 `source_ref` 以 `connector://` 开头。

状态规则：

- Connector 已连接但没有真实 active source → `等待首次读取`；
- 至少一份 active 在线 source → `N 份在线资料可用`；
- 只有 active 上传文件 → `当前仅文件补充`，可以继续但推荐连接在线来源；
- material 状态读取失败 → `状态待核对`。

### 4.3 业务理解输入

该阶段只能表达：

**真实资料是否已经进入 QualiBug 业务理解输入主链。**

规则：

- 至少一份真实 `active source` → 输入主链已建立；
- 没有真实 active source → 等待可读资料；
- material inventory 读取失败 → 无法确认。

前端不得再维护固定业务资料白名单，例如：

`PRD / API / DB / 协作文档 / 历史 Bug`

也不能把这几类或任何有限集合称为“企业核心资料全集”。UI/UX 设计、原型、测试资料、架构资料、权限说明、流程文档以及后端未来新增的其他类型都必须可以正常进入输入展示。

**输入可用不等于业务理解正确或完整。**

## 5. 动态资料类型分布

资料类型展示必须完全数据驱动。

`MaterialsOnboardingHandoff` 从所有真实 active source 读取 `source_type`，动态形成类型分布：

- 常见类型可以有中文友好别名；
- `ui_ux` 可以显示为 `UI / UX 设计`；
- 未知类型必须原样展示；
- 没有 `source_type` 的资料显示为“未分类资料”；
- 新后端类型不需要前端增加白名单才能出现。

允许展示：

`已观察到 N 类 active 资料`

但不得使用固定分母，例如：

- `3/5 类核心输入`；
- `5 类资料完成率`；
- `核心五类资料覆盖率`。

资料类型数量也不得成为新的 Ready/Block 判定。

## 6. Materials → 系统与环境交接

`MaterialsOnboardingHandoff` 只在 `/materials` 生效，并同时读取：

- `getKnowledgeAsset(project)`；
- `listKnowledgeConnectors(project)`。

两者使用 `Promise.allSettled` 保留部分真实状态：一项读取失败不得抹掉另一项已经成功读取到的事实。

它至少区分：

- `onlineActive`：来自在线连接器的 active source；
- `uploadedActive`：非在线来源的 active 文件补充资料；
- `activeTypeCounts / observedTypeCount`：动态资料类型结构；
- `connectorCount`：真实在线 Connector 数量。

状态规则：

- 无资料、无连接器：主动作连接在线资料；
- 有 Connector、无可读资料：显示“已连接 / 等待首次读取”；
- processing：提示仍在处理；
- failed/degraded：显示异常并允许重新核对；
- 至少一份在线 active 且无更高优先级异常：输入主链可建立，进入 `/settings`；
- 只有上传文件且 active：文件资料可用，推荐连接在线资料，同时保留继续路径；
- 不允许因为某个具体资料类型未出现而插入额外阻塞；
- 已经配置过系统的客户最终是否能运行仍由 `/campaigns` preflight 决定。

资料状态每 5 秒重新核对，避免同步/上传完成后客户长期看到旧状态。

## 7. 当前最大阻塞

资料首页只突出一个当前最高优先级动作。

优先级：

1. 状态读取失败；
2. 授权 / 权限问题；
3. 资料源暂停或关闭；
4. Connector 同步失败 / material failed-degraded；
5. 下游刷新未完成；
6. 同步 / processing；
7. 部分资源未支持；
8. 没有任何真实资料、也没有 Connector；
9. Connector 已连接但尚无真实 active source；
10. 当前只有文件补充；
11. 已有真实 active source → 下一步进入系统与环境。

**固定资料类型不足不得出现在这个阻塞优先级中。**

## 8. Settings 接入向导

Settings 必需步骤只有三项：

1. 系统地址；
2. 测试账号；
3. 企业资料。

数据库是可选增强，不阻塞首次体验。

向导读取：

- `listConnectors(project)`；
- `getServiceCredentials(project)`；
- `getKnowledgeAsset(project)`；
- `listKnowledgeConnectors(project)`。

企业资料状态区分：

- 已连接在线资料源数量；
- 已形成的在线资料数量；
- 文件补充数量；
- 资料总数。

资料完成仍按“至少一个真实可读 source 存在”判定；连接器已存在但尚未产生可读 source 时只能显示同步中间态。仅有文件补充时可以 Ready，但必须提示在线资料是推荐主来源。

主 CTA：

- 缺系统地址 → 接入系统；
- 缺测试账号 → 补充测试账号；
- 缺企业资料且没有在线连接器 → **连接企业在线资料**；
- 在线连接器已存在但尚无可读资料 → **查看在线资料同步**；
- 三项完成 → 继续运行前检查；
- 任一状态读取失败 → 先重新核对。

## 9. 运行前检查是最终执行权威

前端 onboarding 完成不等于扫描一定可运行。

进入 `/campaigns` 后必须继续满足真实 `preflight.ready === true`；否则：

- 主扫描按钮 disabled；
- handler fail-closed；
- 显示真实 blocker；
- 引导回 Settings 补充必要条件。

任何 onboarding UI 都不得为了缩短流程绕过这一门禁，也不得用资料类型数量自行创造新门禁。

## 10. 首次使用四步引导

Dashboard `JourneyStrip`：

1. 接入被测系统；
2. **连接企业资料**；
3. 运行前检查并检测；
4. 查看结果与发布建议。

第 2 步：

`优先连接企业在线文档或知识库持续同步，缺失资料再用文件上传补充`

动作进入 `/materials`，使用“连接资料源”而非“上传资料”作为主 CTA。

第 4 步进入 `/dashboard`，不能假设一定存在 Finding。

## 11. Materials 页面顺序合同

稳定顺序：

1. 页面总览与资料就绪状态 / 动态资料类型结构；
2. 在线连接器 / 在线资料连接、授权、范围、同步与健康状态；
3. 文件补充上传；
4. 在线与文件统一后的资料清单。

文件补充上传文案必须说明其用途是补充在线来源没有覆盖的内容。

## 12. 全局导航层级

侧边栏主流程：

`系统总览 → 运行中心 → 问题清单 → 证据中心 → 发布门禁`

项目接入：

`企业资料 → 系统与环境`

高级视图：

`覆盖矩阵 → 后台任务`

发布门禁属于客户主流程。

## 13. 全局状态真实性

Sidebar 必须区分：

- 未选择客户；
- 检测进行中；
- 已确认 P0；
- 已确认普通缺陷；
- 后台补证中；
- 已有真实扫描结果但当前无已确认问题；
- 从未形成扫描结果。

0 Finding 的已完成扫描不得显示成“等待首次验证”。

## 14. 回归契约

`test:settings-onboarding` 已进入 `npm run ci:gate`，至少锁定：

- 三个必需 onboarding 状态来自真实接口；
- 区分已连接在线来源和已形成真实可读资料；
- Connector 存在但尚未 materialize 时不得提前 Ready；
- Materials 首页展示三层状态；
- Connector/material 读取失败独立表达；
- 任意真实 active source 可以进入输入主链；
- 不存在固定 `BUSINESS_CONTEXT_TYPES` 白名单；
- 不存在固定五类输入计数模型；
- `source_type` 动态计数，未知类型原样展示；
- UI/UX 等类型可以使用友好展示别名，但不是允许名单；
- 不存在固定 `/5` 分母；
- 资料类型数量不得成为运行或 Ready 门禁；
- 在线来源和文件补充保持区分；
- 只有文件补充时不阻塞首次运行；
- Settings 完成态可进入真实运行前检查；
- 状态读取失败必须先重新核对；
- Settings 不得重新出现第二套资料上传；
- Materials 在线连接器必须排在文件上传之前；
- JourneyStrip 必须使用“连接企业资料 / 连接资料源”；
- Release Gate 保持主流程；
- Run Center 仍由真实 preflight fail-closed。

## 15. 非目标

本 SPEC 不涉及：

- 企业必须提供哪些固定资料类型；
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
