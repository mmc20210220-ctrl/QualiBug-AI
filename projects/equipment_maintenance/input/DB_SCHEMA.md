# 设备维护工单系统 - 数据库Schema

## 表结构

### maintenance_tickets (维护工单表)
```sql
CREATE TABLE maintenance_tickets (
    ticket_ref        VARCHAR(32) PRIMARY KEY,
    equipment_ref     VARCHAR(32) NOT NULL REFERENCES equipment(equipment_ref),
    title             VARCHAR(200) NOT NULL,
    description       TEXT,
    ticket_status     VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    priority_level    VARCHAR(10) NOT NULL DEFAULT 'NORMAL',
    sla_hours         INTEGER,
    requester_badge   VARCHAR(32) NOT NULL,
    technician_badge  VARCHAR(32) REFERENCES technicians(technician_badge),
    department        VARCHAR(50),
    labor_hours       DECIMAL(6,2),
    resolution_note   TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    submitted_at      TIMESTAMP,
    assigned_at       TIMESTAMP,
    started_at        TIMESTAMP,
    completed_at      TIMESTAMP,
    closed_at         TIMESTAMP
);

-- 状态约束
CHECK (ticket_status IN ('DRAFT','SUBMITTED','ASSIGNED','IN_PROGRESS','PENDING_PARTS','COMPLETED','CLOSED'))
CHECK (priority_level IN ('LOW','NORMAL','HIGH','URGENT'))
-- URGENT优先级SLA约束
CHECK (priority_level != 'URGENT' OR sla_hours <= 4)
```

### equipment (设备台账表)
```sql
CREATE TABLE equipment (
    equipment_ref     VARCHAR(32) PRIMARY KEY,
    equipment_name    VARCHAR(100) NOT NULL,
    equipment_status  VARCHAR(20) NOT NULL DEFAULT 'OPERATIONAL',
    department        VARCHAR(50),
    location_code     VARCHAR(30),
    asset_tag         VARCHAR(50) UNIQUE,
    installed_at      TIMESTAMP,
    last_maintained_at TIMESTAMP
);

CHECK (equipment_status IN ('OPERATIONAL','UNDER_REPAIR','SCRAPPED'))
```

### technicians (维修技师表)
```sql
CREATE TABLE technicians (
    technician_badge  VARCHAR(32) PRIMARY KEY,
    technician_name   VARCHAR(50) NOT NULL,
    technician_status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE',
    skill_level       INTEGER NOT NULL DEFAULT 1,
    department        VARCHAR(50),
    certified_at      TIMESTAMP
);

CHECK (technician_status IN ('AVAILABLE','ON_DUTY','ON_LEAVE'))
CHECK (skill_level BETWEEN 1 AND 5)
```

### spare_part_usage (备件消耗表)
```sql
CREATE TABLE spare_part_usage (
    usage_ref         VARCHAR(32) PRIMARY KEY,
    ticket_ref        VARCHAR(32) NOT NULL REFERENCES maintenance_tickets(ticket_ref),
    part_code         VARCHAR(32) NOT NULL,
    consumed_qty      INTEGER NOT NULL,
    unit_price        DECIMAL(10,2) NOT NULL,
    line_cost         DECIMAL(10,2) NOT NULL,
    recorded_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    
    -- 同一工单同一备件只能记录一次
    UNIQUE (ticket_ref, part_code)
);

CHECK (consumed_qty > 0)
CHECK (unit_price >= 0)
CHECK (line_cost = consumed_qty * unit_price)
```

### settlements (费用结算表)
```sql
CREATE TABLE settlements (
    settlement_ref    VARCHAR(32) PRIMARY KEY,
    ticket_ref        VARCHAR(32) NOT NULL UNIQUE REFERENCES maintenance_tickets(ticket_ref),
    labor_hours       DECIMAL(6,2) NOT NULL,
    hourly_rate       DECIMAL(10,2) NOT NULL,
    labor_cost        DECIMAL(10,2) NOT NULL,
    parts_cost        DECIMAL(10,2) NOT NULL,
    total_charge      DECIMAL(10,2) NOT NULL,
    settlement_status VARCHAR(20) NOT NULL DEFAULT 'PENDING_APPROVAL',
    approved_by       VARCHAR(32),
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    approved_at       TIMESTAMP
);

CHECK (settlement_status IN ('PENDING_APPROVAL','APPROVED','REJECTED'))
CHECK (labor_cost = labor_hours * hourly_rate)
CHECK (total_charge = labor_cost + parts_cost)
CHECK (total_charge >= 0)
```

## 索引

```sql
-- 工单查询优化
CREATE INDEX idx_tickets_equipment ON maintenance_tickets(equipment_ref);
CREATE INDEX idx_tickets_status ON maintenance_tickets(ticket_status);
CREATE INDEX idx_tickets_requester ON maintenance_tickets(requester_badge);
CREATE INDEX idx_tickets_technician ON maintenance_tickets(technician_badge);

-- 活跃工单唯一约束（同一设备只能有一个活跃工单）
CREATE UNIQUE INDEX idx_active_ticket_per_equipment 
ON maintenance_tickets(equipment_ref) 
WHERE ticket_status NOT IN ('CLOSED');
```

## 实体关系

```
equipment (1) ──── (0..1 active) maintenance_tickets
maintenance_tickets (1) ──── (0..*) spare_part_usage
maintenance_tickets (1) ──── (0..1) settlements
technicians (1) ──── (0..*) maintenance_tickets
```

## 测试数据

### 设备
| equipment_ref | equipment_name | equipment_status | department |
|---------------|----------------|------------------|------------|
| EQ-2024-001 | 数控机床A | OPERATIONAL | 生产部 |
| EQ-2024-002 | 注塑机B | OPERATIONAL | 生产部 |
| EQ-2024-003 | 空压机C | SCRAPPED | 动力部 |

### 技师
| technician_badge | technician_name | technician_status | skill_level |
|------------------|-----------------|-------------------|-------------|
| TECH-2001 | 张工 | AVAILABLE | 3 |
| TECH-2002 | 李工 | AVAILABLE | 2 |
| TECH-2003 | 王工 | ON_LEAVE | 4 |

### 测试账号
| 角色 | Token | 说明 |
|------|-------|------|
| requester | Bearer req-token-001 | 报修人 |
| technician | Bearer tech-token-001 | 技师 |
| supervisor | Bearer sup-token-001 | 主管 |
| admin | Bearer admin-token-001 | 管理员 |
