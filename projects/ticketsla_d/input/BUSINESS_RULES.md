# TicketSLA 工单与SLA管理系统 - 业务规则文档

## 系统概述

TicketSLA是一个企业级工单与SLA管理系统，支持多租户、多角色的客户服务流程管理。

## 核心实体

| 实体 | 说明 |
|------|------|
| Ticket | 工单，核心业务实体 |
| SLA | 服务等级协议 |
| Assignment | 工单分配记录 |
| Escalation | 工单升级记录 |
| Comment | 工单评论 |
| Attachment | 工单附件 |
| Customer | 客户 |
| Agent | 客服代理 |
| Team | 客服团队 |
| Notification | 通知 |

## 业务角色

| 角色 | 权限说明 |
|------|----------|
| CUSTOMER | 客户，可创建工单、查看自己的工单、关闭/重开工单 |
| AGENT | 客服代理，可处理分配的工单、添加评论和附件 |
| SUPERVISOR | 主管，可分配工单、升级工单、转移工单、管理团队 |
| ADMIN | 管理员，可管理SLA、团队、客户，拥有全部权限 |

## 工单状态机

```
OPEN → ASSIGNED → IN_PROGRESS → RESOLVED → CLOSED
                                    ↓
                                 REOPEN → OPEN
```

### 状态转换规则

| 规则ID | 规则类型 | 规则描述 |
|--------|----------|----------|
| BR-TKT-001 | STATE_TRANSITION | 只有OPEN状态的工单可以被分配 |
| BR-TKT-002 | STATE_TRANSITION | 只有ASSIGNED状态的工单可以开始处理 |
| BR-TKT-003 | STATE_TRANSITION | 只有IN_PROGRESS状态的工单可以被解决 |
| BR-TKT-004 | STATE_TRANSITION | 只有RESOLVED状态的工单可以被关闭 |
| BR-TKT-005 | STATE_TRANSITION | 只有RESOLVED或CLOSED状态的工单可以被重开 |
| BR-TKT-006 | STATE_TRANSITION | 已关闭的工单不能添加评论 |
| BR-TKT-007 | STATE_TRANSITION | 已解决或已关闭的工单不能被升级 |

## SLA规则

| 规则ID | 规则类型 | 规则描述 |
|--------|----------|----------|
| BR-SLA-001 | TEMPORAL | 高优先级工单响应时间不超过2小时 |
| BR-SLA-002 | TEMPORAL | 高优先级工单解决时间不超过24小时 |
| BR-SLA-003 | TEMPORAL | 中优先级工单响应时间不超过4小时 |
| BR-SLA-004 | TEMPORAL | 中优先级工单解决时间不超过48小时 |
| BR-SLA-005 | TEMPORAL | 重开工单应重新计算SLA截止时间 |
| BR-SLA-006 | TEMPORAL | SLA解决时间必须大于等于响应时间 |

## 授权规则

| 规则ID | 规则类型 | 规则描述 |
|--------|----------|----------|
| BR-AUTH-001 | AUTHORIZATION | 只有客户可以创建工单 |
| BR-AUTH-002 | AUTHORIZATION | 只有主管或管理员可以分配工单 |
| BR-AUTH-003 | AUTHORIZATION | 只有被分配的代理可以开始处理工单 |
| BR-AUTH-004 | AUTHORIZATION | 只有被分配的代理可以解决工单 |
| BR-AUTH-005 | AUTHORIZATION | 只有客户或主管可以关闭工单 |
| BR-AUTH-006 | AUTHORIZATION | 只有客户可以重开工单 |
| BR-AUTH-007 | AUTHORIZATION | 只有主管可以升级工单 |
| BR-AUTH-008 | AUTHORIZATION | 只有主管可以转移工单 |
| BR-AUTH-009 | AUTHORIZATION | 只有管理员可以创建SLA |
| BR-AUTH-010 | AUTHORIZATION | 只有管理员可以创建团队 |
| BR-AUTH-011 | AUTHORIZATION | 只有管理员可以创建客户 |
| BR-AUTH-012 | AUTHORIZATION | 客户只能查看自己的工单 |
| BR-AUTH-013 | AUTHORIZATION | 客户只能关闭自己的工单 |
| BR-AUTH-014 | AUTHORIZATION | 客户只能重开自己的工单 |
| BR-AUTH-015 | AUTHORIZATION | 只能删除自己上传的附件（主管/管理员除外）|

## 租户隔离规则

| 规则ID | 规则类型 | 规则描述 |
|--------|----------|----------|
| BR-TENANT-001 | TENANT_ISOLATION | 客户只能查看本租户的工单 |
| BR-TENANT-002 | TENANT_ISOLATION | 不能分配跨租户的代理 |
| BR-TENANT-003 | TENANT_ISOLATION | 不能合并跨租户的工单 |
| BR-TENANT-004 | TENANT_ISOLATION | 不能添加跨租户的团队成员 |
| BR-TENANT-005 | TENANT_ISOLATION | 不能查看跨租户的客户信息 |
| BR-TENANT-006 | TENANT_ISOLATION | 工单列表必须按租户过滤 |

## 跨实体一致性规则

| 规则ID | 规则类型 | 规则描述 |
|--------|----------|----------|
| BR-CONS-001 | CROSS_ENTITY | 分配工单时必须更新代理工作负载 |
| BR-CONS-002 | CROSS_ENTITY | 关闭工单时必须减少代理工作负载 |
| BR-CONS-003 | CROSS_ENTITY | 转移工单时必须更新新旧代理工作负载 |
| BR-CONS-004 | CROSS_ENTITY | 转移工单时必须停用旧分配记录 |
| BR-CONS-005 | CROSS_ENTITY | 升级工单时应提升工单优先级 |
| BR-CONS-006 | CROSS_ENTITY | 合并工单时应继承较高优先级 |
| BR-CONS-007 | CROSS_ENTITY | 解决工单时应检查SLA合规性 |

## 数据完整性规则

| 规则ID | 规则类型 | 规则描述 |
|--------|----------|----------|
| BR-DATA-001 | VALIDATION | 工单优先级必须是LOW/MEDIUM/HIGH/CRITICAL之一 |
| BR-DATA-002 | VALIDATION | 评论内容不能超过5000字符 |
| BR-DATA-003 | VALIDATION | 附件大小不能超过10MB |
| BR-DATA-004 | VALIDATION | 客户邮箱必须唯一 |
| BR-DATA-005 | VALIDATION | 代理容量不能超过最大工单数 |
| BR-DATA-006 | VALIDATION | 团队成员不能重复添加 |

## 并发与幂等规则

| 规则ID | 规则类型 | 规则描述 |
|--------|----------|----------|
| BR-CONC-001 | CONCURRENCY | 工单更新必须使用版本号进行乐观锁控制 |
| BR-CONC-002 | IDEMPOTENCY | 重复解决同一工单应返回409冲突 |
| BR-CONC-003 | IDEMPOTENCY | 重复关闭同一工单应返回409冲突 |

## 补偿规则

| 规则ID | 规则类型 | 规则描述 |
|--------|----------|----------|
| BR-COMP-001 | COMPENSATION | 移除有活跃工单的代理前应检查并阻止 |
| BR-COMP-002 | COMPENSATION | 批量分配失败时应回滚已分配的工单 |

## 状态字段定义

### Ticket.status
- OPEN: 新建，待分配
- ASSIGNED: 已分配给代理
- IN_PROGRESS: 代理正在处理
- RESOLVED: 已解决，待客户确认
- CLOSED: 已关闭

### SLA.status
- ACTIVE: 生效中
- BREACHED: 已违约
- EXPIRED: 已过期

### Escalation.status
- PENDING: 待处理
- ESCALATED: 已升级
- RESOLVED: 已解决

### Agent.status
- AVAILABLE: 可用
- BUSY: 忙碌
- OFFLINE: 离线

## 优先级定义

| 优先级 | 响应时间 | 解决时间 |
|--------|----------|----------|
| LOW | 8小时 | 72小时 |
| MEDIUM | 4小时 | 48小时 |
| HIGH | 2小时 | 24小时 |
| CRITICAL | 1小时 | 8小时 |

## 客户等级

| 等级 | 说明 |
|------|------|
| GOLD | 金牌客户，享受高优先级SLA |
| SILVER | 银牌客户，享受中优先级SLA |
| BRONZE | 铜牌客户，享受低优先级SLA |
