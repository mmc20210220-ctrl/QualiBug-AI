# WMS 仓储管理系统 - 测试环境说明

## 服务地址

- API服务: http://localhost:8003
- 健康检查: GET /health

## 认证方式

使用Bearer Token认证：
```
Authorization: Bearer <token>
```

## 测试账号

### ACME组织

| 角色 | 用户名 | Token | 所属仓库 | 说明 |
|------|--------|-------|---------|------|
| OPERATOR | Omar Zhang | operator-omar-token | wh-001 | 仓库操作员 |
| OPERATOR | Olga Li | operator-olga-token | wh-002 | 仓库操作员 |
| MANAGER | Mia Chen | manager-mia-token | wh-001 | 仓库经理 |
| ORDER_MANAGER | Nina Zhao | ordermgr-nina-token | wh-001 | 订单经理 |
| CUSTOMER | Cara Wang | customer-cara-token | - | 客户 |
| ADMIN | Alex Zhou | admin-alex-token | - | 管理员 |
| AUDITOR | Ava Yang | auditor-ava-token | - | 审计员（只读） |

### Globex组织

| 角色 | 用户名 | Token | 所属仓库 | 说明 |
|------|--------|-------|---------|------|
| OPERATOR | Oscar Wu | operator-oscar-token | wh-003 | 仓库操作员 |
| MANAGER | Max Sun | manager-max-token | wh-003 | 仓库经理 |
| ORDER_MANAGER | Noah Xu | ordermgr-noah-token | wh-003 | 订单经理 |
| CUSTOMER | Carl Liu | customer-carl-token | - | 客户 |
| ADMIN | Anna Huang | admin-anna-token | - | 管理员 |

## 初始数据

### 仓库
- wh-001: ACME East Warehouse (容量10000, 已用3500, ACME)
- wh-002: ACME West Warehouse (容量8000, 已用2000, ACME)
- wh-003: Globex Central Warehouse (容量12000, 已用4000, Globex)

### 供应商
- sup-001: Shenzhen Parts Co (ACME, 交期7天)
- sup-002: Guangzhou Materials Ltd (ACME, 交期5天)
- sup-003: Beijing Components Inc (Globex, 交期10天)

### 商品
- prod-001: Circuit Board A (SKU-ELEC-001, ¥45.50, 0.3kg, ACME)
- prod-002: Sensor Module B (SKU-ELEC-002, ¥120.00, 0.5kg, ACME)
- prod-003: Gear Assembly C (SKU-MECH-001, ¥78.00, 1.2kg, ACME)
- prod-004: Packaging Box D (SKU-PKG-001, ¥5.00, 0.1kg, Globex)
- prod-005: Protective Wrap E (SKU-PKG-002, ¥3.50, 0.05kg, Globex)

### 库存批次
- batch-001: prod-001 @ wh-001, 数量500, 预留50, AVAILABLE
- batch-002: prod-002 @ wh-001, 数量200, 预留20, AVAILABLE
- batch-003: prod-003 @ wh-002, 数量150, 预留0, AVAILABLE
- batch-004: prod-004 @ wh-003, 数量1000, 预留100, AVAILABLE
- batch-005: prod-005 @ wh-003, 数量2000, 预留0, RECEIVED

### 订单
- ord-001: ORD-2026-001, cust-001, CONFIRMED, ¥575.00, wh-001
- ord-002: ORD-2026-002, cust-002, CREATED, ¥50.00, wh-003
- ord-003: ORD-2026-003, cust-001, ALLOCATED, ¥240.00, wh-001

### 订单行
- ol-001: ord-001, prod-001 x10, ¥455.00
- ol-002: ord-001, prod-002 x1, ¥120.00
- ol-003: ord-002, prod-004 x10, ¥50.00
- ol-004: ord-003, prod-002 x2, ¥240.00

### 预留
- res-001: ord-003, batch-002, prod-002 x2, ACTIVE

### 拣货单
- pick-001: ord-003, wh-001, CREATED, 由op-001创建

## 环境重置

重启服务即可重置所有数据到初始状态：
```bash
python projects/warehouse_e/mock_server.py 8003
```

## 允许的操作范围

- 所有GET请求（读取）
- 所有POST请求（创建和操作）
- 所有PUT请求（更新）
- 所有DELETE请求（删除）

## 禁止的操作

无特殊禁止操作，测试环境可自由使用。
