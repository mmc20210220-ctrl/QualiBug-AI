# QualiBug 系统行为数字孪生驾驶舱升级 Spec

## Why
当前 `frontend/` 已具备商用前端基线、项目工作区、风险页面和 Behavior Space 可视化能力，但整体仍偏“工程报表型控制台”，没有把“客户环境是否可跑”提升为首屏第一关口，也缺少贯穿环境诊断、执行剧场、证据回放和商业交付表达的一致展示模型。

本次变更目标是在不改动后端 Bug Engine 核心逻辑的前提下，把现有前端升级为“先诊断客户环境，再建模系统行为空间，再实时执行探针，再回放 Bug 证据，最后生成可审计交付包”的系统行为数字孪生驾驶舱。

## What Changes
- 将项目首页/工作区重构为“客户环境与运行门禁总览”，把 `Environment Gate / 客户环境门禁` 作为首屏主舞台。
- 新增统一的环境诊断数据合同 `EnvironmentDiagnosticGraph`，并通过 feature model/mock/adapter 驱动首页、环境页、阻断项汇总和 Evidence Drawer。
- 新增客户环境诊断 2.5D 拓扑 MVP，使用 CSS 2.5D + SVG 连线表达至少 10 个环境节点与五种状态。
- 新增贯穿首页、环境页、执行页、风险页的 `Evidence Drawer`，承载状态、影响范围、证据、修复动作和关联 artifact。
- 修正全局产品壳与导航状态，保证任意时刻仅一个主导航项 active，并补齐顶部状态区、右侧证据抽屉、底部实时事件流的统一布局。
- 升级 `Behavior Space` 为分层行为空间视图，明确角色、行为路径、系统模块、证据节点和风险浮标的关系。
- 升级 `Runtime Execution` 为执行剧场，统一 `RuntimeEvent` 模型，并把事件流映射到节点、路径和失败证据。
- 升级单条风险/发现详情为“证据回放时间线”，支持播放、暂停、跳转失败点和复制复现步骤。
- 增强 ROI/交付表达，突出风险成本、节省工时、覆盖率、customer-ready 证据包和仍需客户配合项。
- 强制执行脱敏与可信状态原则：不得展示原始 host、token、cookie、secret；配置未验证不等于 online。

## Impact
- Affected specs: 商用前端产品壳、环境诊断可视化、Behavior Space 可视化、实时执行状态、证据回放、交付与 ROI 展示
- Affected code: `frontend/src/app/(app)/projects/[projectId]/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/behavior-space/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/execution/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/risks/page.tsx`、`frontend/src/app/(app)/projects/[projectId]/risks/[riskId]/page.tsx`、`frontend/src/components/layout/project-nav/ProjectNav.tsx`、`frontend/src/components/layout/TopBar.tsx`、`frontend/src/components/execution/ExecutionRealtimePanel.tsx`、`frontend/src/components/behavior-space/*`、`frontend/src/lib/api/command-center.ts`、`frontend/src/lib/api/command-center-demo.ts`

## ADDED Requirements
### Requirement: 首页以客户环境门禁为第一关口
系统 SHALL 把项目工作区首页定义为“客户环境与运行门禁总览”，首屏优先回答客户环境是否允许启动 runtime、为什么受阻、需要什么修复动作以及修复后能进入哪些探针能力。

#### Scenario: 首屏展示运行门禁
- **WHEN** 用户进入项目工作区首页
- **THEN** 页面首屏显示 `Environment Gate / 客户环境门禁` 主区域
- **AND** 页面明确展示 `runtime_start_allowed`、`readonly_probe_allowed`、`write_probe_allowed`、`p0p1_validation_allowed`
- **AND** 若存在阻断项，页面以高风险视觉样式展示阻断原因与下一步动作
- **AND** 页面使用拓扑、路径或节点化可视表达，而不是仅输出纯文字结论

### Requirement: 环境诊断 2.5D 拓扑
系统 SHALL 提供客户环境诊断 2.5D 拓扑图，作为 runtime 前的第一关诊断舞台，覆盖入口、网络、TLS、认证、账号矩阵、API 可达性、测试数据、快照、清理与最终 runtime 门禁节点。

#### Scenario: 浏览环境诊断拓扑
- **WHEN** 用户进入环境诊断页面
- **THEN** 页面显示至少 10 个环境节点和节点间连线
- **AND** 节点至少支持 `passed`、`warning`、`blocked`、`checking`、`unknown` 五种状态
- **AND** `Runtime Allowed` 节点汇总最终门禁状态
- **AND** 阻断项可映射到具体 remediation action

### Requirement: 统一环境诊断数据合同
系统 SHALL 通过统一的 `EnvironmentDiagnosticGraph`、`EnvironmentDiagnosticNode` 和 edge 数据结构渲染所有环境诊断 UI，并通过 adapter 层隔离 mock 数据与真实 API。

#### Scenario: 页面通过统一模型渲染
- **WHEN** 首页、环境页或阻断项摘要需要展示环境状态
- **THEN** 它们都从同一份 `EnvironmentDiagnosticGraph` 读取门禁、节点和边数据
- **AND** 页面组件不得散落硬编码业务文案或独立的临时状态结构
- **AND** adapter 层可在 mock/real 数据源之间切换而不改动页面组件职责

### Requirement: Evidence Drawer 贯穿关键页面
系统 SHALL 提供统一的 `Evidence Drawer` 作为关键数字、阻断项、环境节点、探针事件和 Finding 详情的下钻入口。

#### Scenario: 从阻断项下钻证据
- **WHEN** 用户点击阻断项计数、环境节点、失败事件或风险条目
- **THEN** 右侧 Evidence Drawer 打开并显示状态、影响范围、证据类型、修复动作与关联 artifact
- **AND** Drawer 支持关闭、复制 action 和跳转到相关页面

### Requirement: 主导航只有一个 active
系统 SHALL 保证全局主导航与当前 URL 严格对应，任意时刻仅允许一个主导航项处于 active 状态。

#### Scenario: 页面切换时更新导航
- **WHEN** 用户在项目工作区、环境诊断、Behavior Space、Runtime 执行、Findings/风险、证据回放、交付包、ROI 页面之间切换
- **THEN** 只有当前页面对应导航项显示 active
- **AND** 其余导航项最多显示 hover 或 disabled，不得同时出现多个高亮入口

### Requirement: Runtime Execution Theater
系统 SHALL 把执行页升级为执行剧场，以统一的 `RuntimeEvent` 模型承载 run 生命周期、probe 路径、快照、请求响应和 finding 落点，并把事件映射到画布节点和边。

#### Scenario: 执行事件驱动画布与证据
- **WHEN** runtime 运行中接收到事件流
- **THEN** 画布实时高亮对应节点或路径
- **AND** 失败、阻断或 finding 事件进入证据面板并可下钻
- **AND** 执行完成后生成 summary card，总结结果与下一步动作

### Requirement: Finding 证据回放时间线
系统 SHALL 为单条 Finding 提供可回放的时间线路径，展示 setup、before snapshot、runtime action、response、after snapshot、invariant violation、reproduction pack 和 delivery manifest 状态。

#### Scenario: 用户回放单个 Finding
- **WHEN** 用户进入单条 Finding 或风险详情页
- **THEN** 页面展示一条可播放、可暂停、可跳转失败点的 replay timeline
- **AND** 每一步都显示时间、actor、API/path、摘要、证据状态和是否 customer-ready
- **AND** 用户可以复制复现步骤

### Requirement: 商业价值表达与客户协作提示
系统 SHALL 在 ROI/交付相关页面展示风险成本、节省工时、覆盖率、customer-ready finding 数量、可交付证据包数量和仍需客户配合项。

#### Scenario: 管理者查看 ROI 页面
- **WHEN** 用户进入 ROI/价值页面
- **THEN** 页面使用业务可读指标总结发现成果、交付价值和剩余阻断项
- **AND** 页面不得做未经 benchmark 证明的夸大性发现率宣称

### Requirement: 完整状态与可信显示
系统 SHALL 为驾驶舱提供 `empty`、`loading`、`error`、`blocked`、`ready`、`running`、`completed` 等完整状态，并坚持“已配置不等于健康在线”的可信显示原则。

#### Scenario: SSE 断开或真实后端不可达
- **WHEN** SSE 连接断开、真实 API 检查失败或环境数据缺失
- **THEN** 页面显示 disconnected/error/unverified 等明确状态
- **AND** 系统不得把仅配置但未通过健康检查的能力显示为 healthy/online

## MODIFIED Requirements
### Requirement: 前端产品壳以环境门禁优先而非报表优先
系统原有商用前端产品壳能力继续保留，但首页主视图 SHALL 从“报告卡片与价值摘要优先”调整为“环境门禁与运行资格优先”，并以统一的顶部状态区、左侧导航、右侧证据抽屉和底部事件流承载跨页面连续体验。

### Requirement: Behavior Space 作为壁垒展示页而非孤立可视化页
系统原有 Behavior Space 可视化能力 SHALL 调整为驾驶舱中的“行为空间建模与证据下钻”页面，必须与环境门禁、执行剧场、证据回放和 ROI 页面共享模型入口与跳转关系，而不是独立的炫技图谱页。

### Requirement: 页面数据访问全部经过 adapter 层
系统现有页面级数据访问 SHALL 继续统一在 adapter/API 层中收敛，并扩展到环境诊断、执行剧场与证据回放场景；页面组件不得直接在视图层散写 fetch 逻辑。

## REMOVED Requirements
### Requirement: 首页以报表卡片作为默认主舞台
**Reason**: 报表型首页无法优先回答“客户环境能不能跑、为何受阻、需要客户修复什么”，会让后续 runtime、证据与交付结论失真。
**Migration**: 将首页首屏迁移为环境门禁总览；原有报告、价值和摘要卡片下移为辅助决策区，继续保留但不再占据第一视觉焦点。
