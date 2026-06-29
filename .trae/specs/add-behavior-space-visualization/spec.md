# Behavior Space Visualization Spec

## Why
当前 `frontend/` 已完成商用前端的价值呈现层、权限、强类型 API、能力中心、风险证据链、执行生命周期和报告 ROI 页面，但还缺少一套统一的“行为空间可视化”语义层，无法把后端的 runtime evidence、environment diagnostics、probe ledger、reproduction pack 以一致方式转成前端 2D/2.5D 可视化对象。

本变更要解决的不是“再加一个炫酷页面”，而是把 QualiBug 的后端成果明确地展示成产品成果：客户系统已经被建模、客户环境已经被诊断、系统行为已经被覆盖、Bug 是在真实路径中暴露、证据可以回放、交付可以审计。

## What Changes
- 新增一套 `Behavior Space Visualization Schema`，统一描述系统节点、业务行为路径、探针执行、风险暴露、证据引用、回放包和审计轨迹。
- 在前端将可视化表达分层：
  - 2D：`React Flow + ELK` 用于业务流、证据链、风险路径、可读下钻
  - 2.5D：`React Three Fiber + Three.js/WebGPU` 用于旗舰演示层，展示环境拓扑、行为穿行、风险暴露点与回放轨迹
- 新增“行为空间”页面，但它不替代首页/报告/表格；它是对产品成果的解释层和放大器。
- 新增后端到前端的数据映射规范，把 runtime evidence、environment diagnostics、probe ledger、reproduction pack 映射到统一 schema。
- 明确价值优先原则：2.5D 只用于强化“真实路径中的成果展示”，不得成为技术炫技层。

## Impact
- Affected specs: 能力成果呈现、风险证据链、执行生命周期、交付审计、前端可视化架构
- Affected code:
  - 新增 `frontend/src/behavior-space/*`
  - 新增 `frontend/src/components/behavior-space/*`
  - 新增 `frontend/src/app/(app)/projects/[projectId]/behavior-space/*`
  - 可能新增/调整 `frontend/src/lib/api/command-center.ts` 或相邻模块，以提供行为空间聚合视图所需数据

## ADDED Requirements
### Requirement: Behavior Space Visualization Schema
系统 SHALL 提供统一的 `Behavior Space Visualization Schema`，把后端运行时、环境、探针、风险、证据、回放和审计对象转为前端可视化语义结构。

#### Scenario: 后端异构数据统一映射
- **WHEN** 前端读取 runtime evidence、environment diagnostics、probe ledger、reproduction pack
- **THEN** 能生成统一的场景对象、节点、边、行为路径、风险点、证据引用、回放引用和审计引用
- **AND** 同一份 schema 可同时驱动 2D 图、2.5D 沙盘和报告摘要

### Requirement: 价值优先的可视化分层
系统 SHALL 采用“2D 为主、2.5D 为增强”的分层策略，以商业价值表达优先于视觉复杂度。

#### Scenario: 管理者看成果
- **WHEN** 管理者查看系统成果
- **THEN** 默认先看到可决策、可读的 2D/文本价值界面
- **AND** 只有在需要解释真实路径、风险暴露和回放时才进入 2.5D 视图

### Requirement: 系统建模成果可视化
系统 SHALL 在行为空间中明确显示客户系统已被建模，包括服务、数据库、队列、外部 API、前端入口和关键依赖。

#### Scenario: 客户系统被建模
- **WHEN** 用户进入行为空间
- **THEN** 能看到系统节点、关键依赖和业务域分区
- **AND** 能定位每个节点与业务行为、风险和证据的关系

### Requirement: 环境诊断成果可视化
系统 SHALL 在行为空间中展示环境诊断结果，包括可测性、认证、网络、健康状态和阻断项。

#### Scenario: 客户环境被诊断
- **WHEN** 用户查看环境层
- **THEN** 能看到当前环境 ready/warning/blocked 状态
- **AND** 能看到阻断原因与建议动作

### Requirement: 行为覆盖与真实路径暴露
系统 SHALL 展示系统行为被覆盖的范围，并标明 Bug 是沿真实路径暴露的，而不是孤立规则命中。

#### Scenario: 系统行为被覆盖
- **WHEN** 用户查看行为路径层
- **THEN** 能看到已覆盖/部分覆盖/未覆盖路径
- **AND** 风险点明确挂在对应真实路径上

### Requirement: 证据可回放
系统 SHALL 在行为空间中将风险、证据链和 reproduction pack 串联起来，支持摘要级回放入口。

#### Scenario: 证据可以回放
- **WHEN** 用户查看某个风险点
- **THEN** 能下钻到证据摘要、回放包入口和复现步骤
- **AND** 不直接暴露未经脱敏的敏感原始证据

### Requirement: 交付可审计
系统 SHALL 将交付、审批、签收和导出轨迹映射到行为空间或关联面板中，证明交付过程可审计。

#### Scenario: 交付可以审计
- **WHEN** 用户查看交付与审计信息
- **THEN** 能看到相关交付记录、审批动作、时间戳和责任主体

## MODIFIED Requirements
### Requirement: 后端能力成果统一呈现
现有“能力中心、风险证据链、执行生命周期、报告 ROI”的成果展示将扩展为带有统一行为空间语义层的展示体系，但仍保持“首页/报告页优先表达商业价值”的原则。

## REMOVED Requirements
### Requirement: 让 2.5D 视图承担主操作界面
**Reason**: 2.5D 更适合作为解释层与旗舰演示层，不适合作为默认操作和决策界面。
**Migration**: 保持首页/能力中心/风险/执行/报告等页面承担主工作流，行为空间作为下钻入口与高价值演示层。
