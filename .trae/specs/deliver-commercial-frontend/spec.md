# QualiBug 商用展示层交付 Spec

## Why
当前仓库最新代码已经具备 `Phase105A` 静态产品壳、`Phase106A` React/Vite/TypeScript 脚手架和 `Phase106D` 项目级路由，但核心页面仍大量停留在 demo 数据直出、`pre` 文本块和弱交互状态，尚不能作为可商用的完整产品展示层。

本次变更需要在现有前端骨架基础上，补齐真实产品壳、页面级工作流、状态反馈、真实 API / Demo 双模式表达以及商用品质的交互与验收闭环，使展示层能够支撑客户演示、POC、试点交付和后续正式前端演进。

## What Changes
- 以现有 `Phase106` 前端工程骨架为基础，升级为完整 QualiBug 指挥中心展示层，而不是继续停留在脚手架或静态样例。
- 将七个核心产品页面从原始 JSON/`pre` 输出改造为产品化界面，覆盖概览、导入、环境诊断、业务流、执行、风险证据、报告 ROI。
- 建立统一的前端产品壳能力：项目上下文、导航、顶部状态区、全局模式标识、加载/空态/失败/离线态、危险动作确认。
- 强化 Demo 模式与真实 API 模式的可观测性，后端只有真实健康检查成功后才显示在线，配置存在但未验证不得视为健康。
- 建立商用品质验收基线：构建、路由冒烟、API 合同、关键页面 DOM/视觉断言、安全脱敏和响应式可用性。

## Impact
- Affected specs: 前端产品壳、项目工作区、页面级体验、真实 API 运行时、前端验收门禁
- Affected code: `ai_test_asset_center/phase106_frontend_app_scaffold.py`、`ai_test_asset_center/phase106_frontend_project_routes.py`、`ai_test_asset_center/phase106_frontend_api_runtime.py`、相关 `tests/test_phase106*.py`、前端生成产物模板

## ADDED Requirements
### Requirement: 商用级产品展示壳
系统 SHALL 基于现有 `Phase106` 工程骨架输出一个可用于客户演示与试点交付的完整 QualiBug 前端产品壳，而不是仅输出脚手架或静态样例。

#### Scenario: 首屏进入真实产品而非营销页
- **WHEN** 用户打开前端应用
- **THEN** 首屏进入 QualiBug 指挥中心工作区
- **AND** 左侧导航暴露完整产品旅程
- **AND** 顶部区域显示当前项目、运行模式、后端状态和关键动作入口

### Requirement: 页面级产品化改造
系统 SHALL 将核心页面改造为产品化 UI，避免以原始 JSON dump 作为主要界面。

#### Scenario: 核心页面具备业务语义
- **WHEN** 用户进入任一核心页面
- **THEN** 页面展示业务可读的卡片、表格、时间线、状态块、步骤引导或详情面板
- **AND** 页面保留与该工作流相关的核心动作入口
- **AND** 原始 JSON 最多作为辅助调试区，不得成为主视觉主体

### Requirement: Demo 与真实 API 双模式可信表达
系统 SHALL 明确区分 Demo 模式与真实 API 模式，并以真实健康检查结果决定后端在线状态。

#### Scenario: 真实 API 未验证成功
- **WHEN** 仅配置了 API 地址或密钥，但健康检查未成功
- **THEN** 前端显示为离线、错误或未验证状态
- **AND** 不得将后端标记为 healthy / online

#### Scenario: 真实 API 健康检查成功
- **WHEN** 前端成功完成真实健康检查请求
- **THEN** 顶部状态区和相关页面显示后端在线
- **AND** 明确展示数据来自真实 API 模式

### Requirement: 项目级工作区连续性
系统 SHALL 保持项目上下文在导航和页面切换中的连续性，支持项目切换且不丢失主工作区状态。

#### Scenario: 切换项目后继续当前旅程
- **WHEN** 用户在工作区切换当前项目
- **THEN** 当前路由和页面信息架构保持稳定
- **AND** 页面数据刷新到新的项目上下文
- **AND** 用户能够继续完成环境诊断、测试执行、风险审阅和报告查看

### Requirement: 全局状态反馈与安全交互
系统 SHALL 为用户可见的长链路动作提供明确的加载、完成、失败、阻断和确认反馈。

#### Scenario: 执行高风险动作
- **WHEN** 用户触发生成测试计划、执行预检、开始执行、生成报告等动作
- **THEN** 前端展示进行中状态、禁用态或取消保护
- **AND** 危险动作需要显式确认
- **AND** 前端不得渲染原始 secret、token、cookie、session 或 traceback

### Requirement: 商用交付验收基线
系统 SHALL 通过构建、合同、冒烟、状态和响应式检查证明该展示层可作为商用前端基线。

#### Scenario: 交付前验收
- **WHEN** 前端交付包进入验收阶段
- **THEN** 构建、路由冒烟、API 模式、关键页面状态、移动端/桌面端可用性和脱敏检查全部通过
- **AND** 任一关键检查失败时不得宣称已达到商用品质

## MODIFIED Requirements
### Requirement: 前端工程脚手架输出
系统当前会生成 Vite + React + TypeScript 前端脚手架与项目级路由；变更后该输出必须进一步成为“可运行、可演示、可验收的产品前端基线”，而非仅证明工程初始化成功。

## REMOVED Requirements
### Requirement: 以原始 JSON 直出作为主要页面内容
**Reason**: 该方式只能证明数据联通或样例占位，无法支撑客户演示、试点交付与商用前台体验。
**Migration**: 将页面主区域替换为业务语义组件；如需保留 JSON，仅允许放入辅助调试抽屉、开发开关或次级信息区。
