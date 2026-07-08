# Tasks
- [ ] Task 1: 盘点主链模块地图与当前断点：扫描现有前端、后端、数据模型、执行器、证据链、回归链路，输出配置 -> 资料 -> 建模 -> 任务 -> 执行 -> Bug -> 证据 -> 前端 -> 回归的模块映射与断点清单。
  - [ ] SubTask 1.1: 确认项目配置的前端入口、保存接口、持久化模型与执行链读取点
  - [ ] SubTask 1.2: 确认企业资料上传、解析、落库、知识消费与历史 Bug 进入主链的实际调用路径
  - [ ] SubTask 1.3: 确认行为建模、任务规划、执行器、Bug 判断、证据聚合、前端展示、回归入口的现有模块与未接线能力

- [ ] Task 2: 打通项目配置真实驱动执行的断点：确保项目配置被任务规划器、执行器、证据链与回归链统一读取，移除写死 demo URL、账号、路径和本地默认值。
  - [ ] SubTask 2.1: 修复配置读取断点，确保运行入口透传项目级 URL、OpenAPI、账号、数据库与环境约束
  - [ ] SubTask 2.2: 清理执行链与证据链中的硬编码样例参数和行业专用分支
  - [ ] SubTask 2.3: 为“保存配置后立即运行”补最小回归验证，证明执行链读取的是项目配置

- [ ] Task 3: 打通企业资料进入测试计划的断点：确保上传资料不仅落库，还能驱动行为模型、测试任务、风险规则与历史 Bug 回归。
  - [ ] SubTask 3.1: 统一资料解析后的结构化知识对象与项目关联键
  - [ ] SubTask 3.2: 让测试计划能够引用 PRD、接口文档、数据库约束、权限规则和历史 Bug 作为来源依据
  - [ ] SubTask 3.3: 为“上传资料后生成计划”补回归验证，证明资料真实进入主链

- [ ] Task 4: 打通结构化行为模型到测试任务生成的断点：确保输出统一格式的跨行业行为模型，并能转化为可执行任务。
  - [ ] SubTask 4.1: 对齐 Actor、Resource、Action、State、Transition、Invariant、Permission Rule、Data Rule、Risk Point、Oracle 的统一结构
  - [ ] SubTask 4.2: 让任务生成器显式绑定行为节点、来源依据、预期结果、输入数据、风险类型和证据采集要求
  - [ ] SubTask 4.3: 为至少一条 API 路径和一条 UI 路径补生成验证，证明任务不是随机请求

- [ ] Task 5: 打通真实执行到 Execution Result 的断点：把 HTTP/OpenAPI/UI/HAR/截图/DB 快照等现有能力统一接入任务执行结果与状态回写。
  - [ ] SubTask 5.1: 修复任务状态 pending/running/passed/failed/blocked 的回写链
  - [ ] SubTask 5.2: 统一 Execution Result 与任务、项目、行为节点、请求响应、截图/HAR/DB 快照的关联
  - [ ] SubTask 5.3: 为代表性任务补真实执行验证，证明执行结果能在后端与前端同时看到

- [ ] Task 6: 打通 Bug 判断与 Evidence Package 聚合的断点：确保正式 Bug 必须绑定真实证据，并且证据不足会被降级。
  - [ ] SubTask 6.1: 对齐 BugReport、EvidencePackage 与 Execution Result 的关联键和准入规则
  - [ ] SubTask 6.2: 实现或收紧正式 Bug 与疑似问题/证据不足的分流逻辑
  - [ ] SubTask 6.3: 为请求响应、截图/HAR、DB 差异、预期/实际对比、最小复现步骤补闭环验证

- [ ] Task 7: 打通前端展示与回归验证的断点：让客户可以从前端看到真实执行进度、Bug 详情、证据链和修复后回归状态。
  - [ ] SubTask 7.1: 修复前端读取真实 API 的断点，去除 mock 数据和本地推断统计
  - [ ] SubTask 7.2: 补齐 Bug 详情中的 Evidence Package、复现步骤和回归入口展示
  - [ ] SubTask 7.3: 为“点击重新验证”补回归验证，证明能复用原链路并输出 fixed/still failing/flaky/blocked

- [ ] Task 8: 跑通一个 P0 最小商业闭环 Demo：用一个真实项目、一个测试账号、一条 API 或页面路径、一份企业资料样例，完成从配置到回归的全链路验收。
  - [ ] SubTask 8.1: 选择一条当前最可控的真实主链场景，避免扩散到多个无关模块
  - [ ] SubTask 8.2: 输出该场景的链路证据，包括任务、执行结果、Bug、Evidence Package、前端展示与回归结果
  - [ ] SubTask 8.3: 记录仍未打通的链路、剩余风险与下一轮只围绕主链断点的修复建议

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 1
- Task 4 depends on Task 3
- Task 5 depends on Task 2
- Task 5 depends on Task 4
- Task 6 depends on Task 5
- Task 7 depends on Task 5
- Task 7 depends on Task 6
- Task 8 depends on Task 2
- Task 8 depends on Task 3
- Task 8 depends on Task 4
- Task 8 depends on Task 5
- Task 8 depends on Task 6
- Task 8 depends on Task 7
