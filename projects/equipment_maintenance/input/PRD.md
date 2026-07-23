# 设备维护工单管理系统 - 产品需求文档

## 1. 系统概述

本系统用于管理企业设备维护工单的全生命周期。从报修提交、工单分配、维修执行到完工结算，实现设备维护流程的数字化管理。

## 2. 核心业务实体

### 2.1 维护工单 (MaintenanceTicket)
- 工单是系统核心实体，记录设备故障报修和计划维护任务
- 每个工单关联一台设备和一名维修技师
- 工单有严格的状态流转规则

### 2.2 设备台账 (Equipment)
- 记录企业所有设备的基础信息和维护历史
- 设备有唯一资产编号 (asset_tag)
- 设备状态影响工单创建

### 2.3 维修技师 (Technician)
- 记录维修人员信息和技能认证
- 技师有工号 (badge_number) 和技能等级
- 技师状态影响工单分配

### 2.4 备件消耗 (SparePartUsage)
- 记录工单维修过程中消耗的备件
- 每条记录关联工单和备件
- 消耗数量影响库存和费用

### 2.5 费用结算 (Settlement)
- 工单完工后的费用汇总
- 包含人工费 (labor_cost) 和备件费 (parts_cost)
- 总费用 (total_charge) = 人工费 + 备件费

## 3. 状态流转规则

### 3.1 工单状态机
```
DRAFT → SUBMITTED → ASSIGNED → IN_PROGRESS → COMPLETED → CLOSED
                                    ↓
                              PENDING_PARTS → IN_PROGRESS
```

状态转换规则：
- DRAFT → SUBMITTED: 提交后不可撤回编辑
- SUBMITTED → ASSIGNED: 必须指定有效技师
- ASSIGNED → IN_PROGRESS: 技师开始维修
- IN_PROGRESS → PENDING_PARTS: 等待备件
- PENDING_PARTS → IN_PROGRESS: 备件到位
- IN_PROGRESS → COMPLETED: 维修完成
- COMPLETED → CLOSED: 结算完成后关闭
- 已CLOSED工单不可重新打开

### 3.2 设备状态
- OPERATIONAL: 正常运行
- UNDER_REPAIR: 维修中（有活跃工单时自动设置）
- SCRAPPED: 已报废（不可创建新工单）

### 3.3 技师状态
- AVAILABLE: 可分配
- ON_DUTY: 执行中
- ON_LEAVE: 休假（不可分配）

## 4. 业务规则

### 4.1 工单创建规则
- 设备状态为SCRAPPED时禁止创建工单
- 同一设备同时只能有一个活跃工单（状态非CLOSED）
- 优先级为URGENT时sla_hours必须≤4

### 4.2 工单分配规则
- 只能分配状态为AVAILABLE的技师
- 分配后技师状态变为ON_DUTY
- 工单完成或关闭后技师状态恢复AVAILABLE

### 4.3 费用计算规则
- labor_cost = labor_hours × hourly_rate
- parts_cost = Σ(备件单价 × 消耗数量)
- total_charge = labor_cost + parts_cost
- 费用必须≥0，不允许负数

### 4.4 备件消耗规则
- 消耗数量必须>0
- 消耗数量不能超过库存可用量
- 同一工单同一备件只能记录一次

### 4.5 结算规则
- 只有COMPLETED状态工单可以结算
- 结算后total_charge不可修改
- 结算完成是CLOSED的前置条件

## 5. 角色权限

| 角色 | 权限 |
|------|------|
| requester | 创建/查看自己的工单 |
| technician | 查看/更新分配给自己的工单 |
| supervisor | 分配工单、审批结算、查看所有工单 |
| admin | 全部权限 |

### 5.1 数据隔离
- requester只能看到自己创建的工单
- technician只能看到分配给自己的工单
- 不同部门(department)的supervisor只能看到本部门工单

## 6. 接口清单

详见 API_SPEC.md

## 7. 数据模型

详见 DB_SCHEMA.md
