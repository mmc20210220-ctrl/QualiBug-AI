# QualiBug 前端首次接入单主线 SPEC

状态：已实现  
范围：仅前端信息架构、状态展示、导航和交互；不定义或修改任何后端 Bug 发现、扫描、Oracle、Finding、Coverage、Release Gate 或 Regression Gate 逻辑。

## 1. 目标

首次客户从创建/选择项目开始，前端必须始终给出一个明确、可执行、不会绕过真实后端门禁的下一步。

主闭环：

`选择/创建客户 → 系统与环境 + 企业资料 → 运行前检查 → 标准扫描 → 价值总览 → 问题/证据 → 发布门禁`

“系统与环境”和“企业资料”都是首次扫描的前置能力，允许客户按实际情况先完成任一项；前端必须在完成一项后主动把客户交接到另一项，而不是留下死路。

## 2. 企业资料唯一入口

企业资料的唯一正式入口是：

`/materials?project=<project>`

Settings 不再维护第二套文件上传和知识入库 UI，只保留：

- 客户项目选择/创建；
- 企业资料状态摘要；
- “打开企业资料”导航。

禁止在 Settings 重新引入 `type="file"`、`ingestKnowledgeFiles` 或另一套资料解析/上传流程。

## 3. Materials → 系统与环境交接

`MaterialsOnboardingHandoff` 只在 `/materials` 生效，并从真实 `getKnowledgeAsset(project)` 读取资料状态。

状态规则：

- 无资料：不伪造完成提示，由 Materials 自身资料入口引导接入；
- processing：提示仍在处理，可以同时配置系统与环境；
- failed/degraded：显示异常并允许重新核对，不把失败解释成“没有资料”；
- 至少一份 active 且无 processing/failed：显示“企业资料已接入”，主动作进入 `/settings`；
- 已经配置过系统的客户可直接进入 `/campaigns`，但是否能运行仍由真实 preflight 决定。

资料状态每 5 秒重新核对，避免上传/同步完成后客户仍长时间看到旧状态。

## 4. Settings 接入向导

Settings 必需步骤只有三项：

1. 系统地址；
2. 测试账号；
3. 企业资料。

数据库是可选增强，不阻塞首次体验。

向导分别从真实接口读取：

- `listConnectors(project)`；
- `getServiceCredentials(project)`；
- `getKnowledgeAsset(project)`。

只有三个必需状态都成功读取且均完成时，前端才显示“基础接入已完成”。

主 CTA 按缺失状态变化：

- 缺系统地址 → 接入系统；
- 缺测试账号 → 补充测试账号；
- 缺企业资料 → 打开 Materials；
- 三项完成 → 继续运行前检查；
- 任一状态读取失败 → 先重新核对，不把读取失败当成配置缺失。

## 5. 运行前检查是最终执行权威

前端 onboarding 完成不等于扫描一定可运行。

进入 `/campaigns` 后必须继续满足真实 `preflight.ready === true`；否则：

- 主扫描按钮 disabled；
- handler fail-closed；
- 显示真实 blocker；
- 引导回 Settings 补充必要条件。

任何 onboarding UI 都不得为了缩短流程绕过这一门禁。

## 6. 首次使用四步引导

Dashboard `JourneyStrip` 使用真实流程措辞：

1. 接入被测系统；
2. 导入企业资料；
3. 运行前检查并检测；
4. 查看结果与发布建议。

第 4 步必须进入 `/dashboard`，不能直接假设一定存在 Finding。问题清单和证据中心属于结果后的条件分支。

## 7. 全局导航层级

侧边栏主流程：

`系统总览 → 运行中心 → 问题清单 → 证据中心 → 发布门禁`

项目接入：

`企业资料 → 系统与环境`

高级视图：

`覆盖矩阵 → 后台任务`

发布门禁属于客户主流程，不得重新降级到“高级视图”。

## 8. 全局状态真实性

Sidebar 必须区分：

- 未选择客户；
- 检测进行中；
- 已确认 P0；
- 已确认普通缺陷；
- 后台补证中；
- 已有真实扫描结果但当前无已确认问题；
- 从未形成扫描结果。

因此，0 Finding 的已完成扫描不得再显示成“等待首次验证”。

## 9. 回归契约

`test:settings-onboarding` 已进入 `npm run ci:gate`，至少锁定：

- 三个必需 onboarding 状态来自真实接口；
- Settings 完成态必须有可点击的“继续运行前检查”；
- 状态读取失败必须先重新核对；
- Materials 与 Settings 之间保留 project 上下文；
- Settings 不得重新出现第二套资料上传；
- Materials handoff 不得把 processing/failed 当 clean ready；
- JourneyStrip 必须先经过运行前检查，结果步骤必须回 Dashboard；
- Release Gate 必须属于主流程；
- clean scan 不得在 Sidebar 显示“等待首次验证”；
- Run Center 仍由真实 preflight fail-closed。

## 10. 非目标

本 SPEC 不涉及：

- Bug 发现率；
- 业务理解算法；
- 场景生成；
- Oracle / Observer / Experiment；
- Finding 成立条件；
- Coverage 计算；
- Release / Regression Gate 后端判定；
- 扫描执行状态机。

上述能力由后端其他模块/Agent 负责，前端只负责准确消费和表达。
