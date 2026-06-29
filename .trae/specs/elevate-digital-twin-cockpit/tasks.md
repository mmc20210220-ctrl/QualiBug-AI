# Tasks
- [x] Task 1: 盘点当前驾驶舱基线与增量范围
  - [x] SubTask 1.1: 对齐现有 `projects/[projectId]` 首页、环境诊断、Behavior Space、Execution、风险详情和 ROI 页面职责
  - [x] SubTask 1.2: 标记可以复用的现有组件、路由与数据访问层，避免重写整个前端
  - [x] SubTask 1.3: 输出本次变更仅触达显示层、导航、adapter、可视化组件，不改后端 bug engine 核心逻辑

- [x] Task 2: 重构全局产品壳与导航状态
  - [x] SubTask 2.1: 调整 `App Shell` 布局为 TopBar + SideNav + MainCanvas + RightEvidenceDrawer + BottomEventStream
  - [x] SubTask 2.2: 修复主导航 active 规则，确保任意时刻仅一个主导航项高亮
  - [x] SubTask 2.3: 补齐 hover、active、disabled、loading 和 disconnected/error 状态表达

- [x] Task 3: 建立环境诊断 feature 数据模型与 adapter
  - [x] SubTask 3.1: 新建 `features/environment-diagnostics/model.ts`，定义 `GateStatus`、`EnvironmentDiagnosticNode`、`EnvironmentDiagnosticGraph`
  - [x] SubTask 3.2: 新建 mock 数据与 adapter，支持 demo/real 切换
  - [x] SubTask 3.3: 让首页、环境页和阻断项摘要统一从 `EnvironmentDiagnosticGraph` 渲染

- [x] Task 4: 实现首页“客户环境与运行门禁总览”
  - [x] SubTask 4.1: 把项目首页首屏升级为 `Environment Gate / 客户环境门禁` 主舞台
  - [x] SubTask 4.2: 明确展示 `runtime_start_allowed`、`readonly_probe_allowed`、`write_probe_allowed`、`p0p1_validation_allowed`
  - [x] SubTask 4.3: 增加阻断项列表、修复动作摘要和可进入探针能力说明

- [x] Task 5: 实现客户环境诊断 2.5D 拓扑 MVP
  - [x] SubTask 5.1: 新建 `EnvironmentTopology2D5.tsx`，使用 CSS 2.5D + SVG 连线完成节点与路径展示
  - [x] SubTask 5.2: 支持至少 10 个节点与五种状态：passed、warning、blocked、checking、unknown
  - [x] SubTask 5.3: 支持筛选全部、阻断、警告、已通过，并让 `Runtime Allowed` 节点汇总最终门禁

- [x] Task 6: 建立统一 Evidence Drawer 交互
  - [x] SubTask 6.1: 新建 `EvidenceDrawer` 与通用证据卡片结构
  - [x] SubTask 6.2: 接通首页阻断项、环境节点、执行失败事件和风险条目的点击下钻
  - [x] SubTask 6.3: 支持关闭、复制 remediation action、跳转到关联页面与 artifact

- [x] Task 7: 升级 Behavior Space 为分层行为空间页
  - [x] SubTask 7.1: 建立角色层、行为层、系统层、证据层、风险层的图谱 schema 或映射规则
  - [x] SubTask 7.2: 支持路径高亮、风险类型筛选和 coverage 百分比展示
  - [x] SubTask 7.3: 让 bug/证据节点可跳转到 Evidence Replay

- [x] Task 8: 升级 Runtime Execution 为执行剧场
  - [x] SubTask 8.1: 定义统一 `RuntimeEvent` 数据模型与 mock/SSE adapter
  - [x] SubTask 8.2: 把事件映射到画布节点、边和底部事件流
  - [x] SubTask 8.3: 为失败、阻断和 finding 事件提供高亮与证据下钻，并在结束后生成 summary card

- [x] Task 9: 升级单条 Finding 为证据回放页
  - [x] SubTask 9.1: 建立 replay timeline 数据模型，覆盖 setup、snapshot、action、response、violation、manifest
  - [x] SubTask 9.2: 提供播放、暂停、跳转失败点和复制复现步骤
  - [x] SubTask 9.3: 保持与现有风险详情页兼容，必要时提供 findings/risk 路由映射或跳转

- [x] Task 10: 增强 ROI/交付页的商业表达
  - [x] SubTask 10.1: 展示阻断风险数量、事故成本区间、节省工时、覆盖率、customer-ready finding 数量
  - [x] SubTask 10.2: 增加可交付证据包数量与仍需客户配合项
  - [x] SubTask 10.3: 确保不展示夸大性发现率文案与敏感信息

- [x] Task 11: 完善演示态、异常态与回归验证
  - [x] SubTask 11.1: 补齐 empty、loading、error、blocked、ready、running、completed 状态
  - [x] SubTask 11.2: 为 SSE 连接增加 disconnected/error 处理，并验证 demo mock 数据可完整演示
  - [x] SubTask 11.3: 增加构建、类型检查、关键路由冒烟、脱敏扫描和主要点击路径回归验证

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2
- Task 4 depends on Task 3
- Task 5 depends on Task 3
- Task 6 depends on Task 4
- Task 6 depends on Task 5
- Task 7 depends on Task 2
- Task 7 depends on Task 6
- Task 8 depends on Task 2
- Task 8 depends on Task 6
- Task 9 depends on Task 6
- Task 9 depends on Task 8
- Task 10 depends on Task 4
- Task 10 depends on Task 9
- Task 11 depends on Task 5
- Task 11 depends on Task 7
- Task 11 depends on Task 8
- Task 11 depends on Task 9
- Task 11 depends on Task 10
