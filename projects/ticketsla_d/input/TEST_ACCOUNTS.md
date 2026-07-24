# TicketSLA 测试环境说明

## 服务地址

- API服务: http://localhost:8002
- 健康检查: GET /health

## 认证方式

使用Bearer Token认证：
```
Authorization: Bearer <token>
```

## 测试账号

### ACME租户

| 角色 | 用户名 | Token | 说明 |
|------|--------|-------|------|
| CUSTOMER | Alice Wang | customer-alice-token | 金牌客户 |
| CUSTOMER | Bob Li | customer-bob-token | 银牌客户 |
| AGENT | Dave Chen | agent-dave-token | 支持团队成员 |
| AGENT | Eve Liu | agent-eve-token | 支持团队成员 |
| SUPERVISOR | Grace Zhao | supervisor-grace-token | 主管 |
| ADMIN | Ivan Zhou | admin-ivan-token | 管理员 |

### Globex租户

| 角色 | 用户名 | Token | 说明 |
|------|--------|-------|------|
| CUSTOMER | Carol Zhang | customer-carol-token | 金牌客户 |
| AGENT | Frank Wu | agent-frank-token | 支持团队成员 |
| SUPERVISOR | Henry Sun | supervisor-henry-token | 主管 |
| ADMIN | Judy Xu | admin-judy-token | 管理员 |

## 初始数据

### 团队
- team-001: ACME Support Team (成员: agent-001, agent-002)
- team-002: Globex Support Team (成员: agent-003)

### SLA
- sla-001: Gold Tier SLA (HIGH优先级, 2h响应, 24h解决)
- sla-002: Silver Tier SLA (MEDIUM优先级, 4h响应, 48h解决)
- sla-003: Globex Gold SLA (HIGH优先级, 2h响应, 24h解决)

### 工单
- ticket-001: Login page not loading (OPEN, HIGH, cust-001)
- ticket-002: Payment failed (ASSIGNED, MEDIUM, cust-002, 分配给agent-001)
- ticket-003: Feature request (IN_PROGRESS, LOW, cust-003, 分配给agent-003)

## 环境重置

重启服务即可重置所有数据到初始状态：
```bash
python mock_server.py 8002
```

## 允许的操作范围

- 所有GET请求（读取）
- 所有POST请求（创建和操作）
- 所有PUT请求（更新）
- 所有DELETE请求（删除）

## 禁止的操作

无特殊禁止操作，测试环境可自由使用。
