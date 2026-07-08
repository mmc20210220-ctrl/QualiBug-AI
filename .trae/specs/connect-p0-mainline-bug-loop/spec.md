# P0 主链 Bug 闭环 Spec

## Why
当前仓库已经具备项目配置、资料导入、执行器、缺陷判定、证据展示、回归等分散能力，但主流程仍存在多处断点，导致系统无法稳定完成“真实执行 -> 真实 Bug -> 可复现证据 -> 回归验证”的最小商业闭环。需要先围绕主链断点收束改造范围，禁止无关重构，优先把现有能力接入同一条可验证链路。

## What Changes
- 新增一套以 P0 最小商业闭环为目标的主链规格，统一约束本轮只修复主链断点
- 明确主链九段的输入、输出、状态与断点判定标准：配置、资料、建模、任务、执行、Bug、证据、前端、回归
- 要求优先复用现有模块，禁止脱离现有系统重写架构，禁止新增孤立能力
- 定义 P0 必须打通的数据对象与跨模块关联键，确保项目配置、知识资产、执行结果、Bug、证据、回归可追溯
- 收紧“真实执行、真实证据、真实复现”的准入规则，证据不足只能标记为疑似问题或待补证
- 规定每轮输出必须包含代码改动、链路进度、测试命令、测试结果、剩余断点与风险
- **BREAKING**: 禁止任何不服务于主链断点的重构、演示数据回退、本地推断口径和行业硬编码继续进入主流程

## Impact
- Affected specs: `add-knowledge-ingest-api`, `enable-continuous-validated-discovery`, `harden-real-bug-evidence-integrity`
- Affected code: 项目配置与运行入口、知识导入链路、行为建模链路、任务规划器、HTTP/OpenAPI/UI 执行器、Bug 判定器、Evidence Package 聚合、命令中心与前端展示、回归验证链路

## ADDED Requirements
### Requirement: P0 主链断点优先级
系统 SHALL 将“项目配置真实生效 -> 企业资料结构化 -> 测试任务生成 -> 真实执行 -> 结构化 Bug -> Evidence Package -> 前端展示 -> 回归复跑”定义为唯一 P0 主链，并且每轮代码提交只能修复该主链上的断点。

#### Scenario: 拒绝无关改造
- **WHEN** 某项修改无法明确说明它打通了主链中的哪一段
- **THEN** 该修改不得进入本轮实现

#### Scenario: 主链断点优先
- **WHEN** 同时存在 UI 美化、结构重构与主链断点
- **THEN** 系统必须优先安排主链断点修复，其他工作延后

### Requirement: 项目配置必须驱动执行
系统 SHALL 从项目配置中读取被测系统 URL、API Base URL、OpenAPI 地址、测试账号、数据库连接和环境约束，并将这些配置透传到建模、任务规划、执行、Bug 判定与证据链阶段。

#### Scenario: 配置驱动真实运行
- **WHEN** 用户在前端保存一个项目配置并点击“运行分析/开始测试”
- **THEN** 后端生成的测试任务与执行请求必须使用该项目配置，而不是任何写死 demo 值、默认 base_url、默认账号或本地样例路径

### Requirement: 企业资料必须进入测试主链
系统 SHALL 将上传的 PRD、接口文档、数据库设计、权限说明、历史 Bug 和测试用例解析为可追溯知识项，并允许测试计划与风险规则引用这些知识项。

#### Scenario: 资料驱动任务生成
- **WHEN** 某项目上传并解析了企业资料
- **THEN** 测试计划中必须能标明任务来源依据，例如 PRD 流程、接口文档、数据库约束、权限规则或历史 Bug

### Requirement: 行为模型必须结构化且跨行业
系统 SHALL 生成统一结构的行为模型，至少包含 Actor、Resource、Action、State、Transition、Invariant、Permission Rule、Data Rule、Risk Point 和 Oracle，并且不得依赖特定行业名称、固定角色或固定业务对象硬编码。

#### Scenario: 跨行业抽象
- **WHEN** 不同企业项目导入不同领域资料
- **THEN** 系统输出的行为模型格式保持一致，并能继续转化为测试任务

### Requirement: 测试任务必须结构化可追踪
系统 SHALL 为每个测试任务保存任务 ID、项目 ID、行为节点、来源依据、执行方式、输入数据、预期结果、风险类型、证据采集要求与状态。

#### Scenario: 任务进入执行队列
- **WHEN** 用户启动一次测试
- **THEN** 系统必须生成一批状态明确的结构化任务，而不是直接随机请求接口或只输出自然语言计划

### Requirement: 执行必须产出真实 Execution Result
系统 SHALL 通过 HTTP/API、OpenAPI、页面访问、表单填写、多账号登录、HAR、截图、DB 快照等现有执行能力真实运行任务，并生成可关联的 Execution Result。

#### Scenario: 真实执行结果落地
- **WHEN** 任一任务执行完成
- **THEN** 系统必须保存请求响应、截图或 HAR、任务状态、异常原因及项目和行为节点关联

### Requirement: Bug Report 必须绑定证据质量
系统 SHALL 输出结构化 Bug Report，并对证据质量进行分级。缺少关键证据时只允许标记为疑似问题、证据不足或待补充执行，不得直接当成正式 Bug。

#### Scenario: 证据不足降级
- **WHEN** 某次执行只有异常文本但缺少请求响应、截图、HAR、DB 差异或清晰判断依据
- **THEN** 该结果不得进入正式 customer-ready Bug 集合

### Requirement: Evidence Package 必须可复现
系统 SHALL 为正式 Bug 聚合 Evidence Package，至少包含环境、账号、触发步骤、输入数据、请求响应、截图或 HAR、预期/实际对比、判断依据、最小复现步骤、回归入口。

#### Scenario: 研发按证据复现
- **WHEN** 研发人员在前端打开某个正式 Bug 的详情
- **THEN** 即使不查看源码，也能根据 Evidence Package 理解触发路径并尝试复现

### Requirement: 前端必须展示真实主链状态
系统 SHALL 让前端展示真实项目配置状态、资料导入状态、行为模型结果、测试任务列表、执行进度、Bug 列表、证据详情和回归状态，并禁止展示 mock 数据或本地推断口径。

#### Scenario: 前端读取真实 API
- **WHEN** 客户从前端查看执行结果
- **THEN** 所有关键数字、状态和证据都必须来自真实后端接口，而不是静态样例或前端自行拼装

### Requirement: Bug 必须支持回归复跑
系统 SHALL 保存正式 Bug 的最小复现任务和回归入口，支持重新执行并比较修复前后结果，输出 fixed、still failing、flaky 或 blocked。

#### Scenario: 修复后重新验证
- **WHEN** 用户对某个已发现 Bug 点击“重新验证”
- **THEN** 系统必须复用原始输入和链路重新执行，并展示新旧结果差异

### Requirement: 每轮提交必须给出可验证交付
系统 SHALL 在每轮主链改造后输出修改文件、修改原因、打通的主链段落、执行的测试命令、测试结果、剩余断点与新增风险。

#### Scenario: 交付可审计
- **WHEN** 一轮主链改造结束
- **THEN** 交付物必须能说明“修了哪一段、如何验证、还有什么没通”

## MODIFIED Requirements
### Requirement: 真实 Bug 与证据准入
系统必须继续遵守“真实 Bug、真实证据、真实可复现”的既有准入规则，并在此基础上增加主链约束：任何 Bug、证据、前端展示或回归入口若无法关联到项目配置、测试任务和 Execution Result，则不得作为正式交付资产。

## REMOVED Requirements
### Requirement: 孤立能力可独立交付
**Reason**: 仅有资料上传、执行器、Bug 文本、截图采集或前端页面的局部可用性，不能代表产品已经具备商业可交付的闭环能力。
**Migration**: 这些模块必须重挂到 P0 主链的统一对象与状态流中，以“可执行、可追溯、可复现、可回归”为唯一验收标准。
