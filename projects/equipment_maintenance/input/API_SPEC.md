# API 接口文档

API Base URL: `http://localhost:9090`

所有需要登录的接口使用：

```http
Authorization: Bearer <token>
```

## Tickets

### POST /api/v2/tickets

创建维护工单。

请求：

```json
{"equipment_ref":"EQ-2024-001","title":"设备异常振动","description":"运行时产生异常振动噪音","priority_level":"HIGH","sla_hours":8,"requester_badge":"EMP-1001"}
```

响应 201：

```json
{"ticket_ref":"TK-20240101-001","equipment_ref":"EQ-2024-001","ticket_status":"DRAFT","priority_level":"HIGH","sla_hours":8,"requester_badge":"EMP-1001","created_at":"2024-01-01T10:00:00Z"}
```

业务规则：
- equipment_ref对应的设备状态不能为SCRAPPED
- 同一equipment_ref不能存在状态非CLOSED的活跃工单
- priority_level为URGENT时sla_hours必须<=4

### GET /api/v2/tickets

查询工单列表。

查询参数：status, equipment_ref, requester_badge, department

响应 200：

```json
{"items":[{"ticket_ref":"TK-20240101-001","ticket_status":"IN_PROGRESS","priority_level":"HIGH"}],"total":1}
```

数据隔离：requester只能看到自己创建的工单，technician只能看到分配给自己的工单。

### GET /api/v2/tickets/:ticket_ref

查询工单详情。

响应 200：完整工单对象

### POST /api/v2/tickets/:ticket_ref/submit

提交工单。前置条件：ticket_status必须为DRAFT。

响应 200：

```json
{"ticket_ref":"TK-20240101-001","ticket_status":"SUBMITTED","submitted_at":"2024-01-01T11:00:00Z"}
```

### POST /api/v2/tickets/:ticket_ref/assign

分配技师。前置条件：ticket_status必须为SUBMITTED，technician状态必须为AVAILABLE。

请求：

```json
{"technician_badge":"TECH-2001"}
```

响应 200：

```json
{"ticket_ref":"TK-20240101-001","ticket_status":"ASSIGNED","technician_badge":"TECH-2001","assigned_at":"2024-01-01T12:00:00Z"}
```

副作用：技师状态变为ON_DUTY。

### POST /api/v2/tickets/:ticket_ref/start-work

开始维修。前置条件：ticket_status必须为ASSIGNED。

响应 200：

```json
{"ticket_status":"IN_PROGRESS","started_at":"2024-01-01T13:00:00Z"}
```

副作用：设备状态变为UNDER_REPAIR。

### POST /api/v2/tickets/:ticket_ref/hold-parts

挂起等待备件。前置条件：ticket_status必须为IN_PROGRESS。

响应 200：

```json
{"ticket_status":"PENDING_PARTS","hold_reason":"等待备件到货"}
```

### POST /api/v2/tickets/:ticket_ref/resume-work

恢复维修。前置条件：ticket_status必须为PENDING_PARTS。

响应 200：

```json
{"ticket_status":"IN_PROGRESS"}
```

### POST /api/v2/tickets/:ticket_ref/complete

完成维修。前置条件：ticket_status必须为IN_PROGRESS。

请求：

```json
{"labor_hours":3.5,"resolution_note":"更换轴承完成"}
```

响应 200：

```json
{"ticket_status":"COMPLETED","labor_hours":3.5,"completed_at":"2024-01-01T16:00:00Z"}
```

副作用：技师状态恢复AVAILABLE，设备状态恢复OPERATIONAL。

### POST /api/v2/tickets/:ticket_ref/close

关闭工单。前置条件：ticket_status必须为COMPLETED，必须已完成结算。

响应 200：

```json
{"ticket_status":"CLOSED","closed_at":"2024-01-01T17:00:00Z"}
```

## Equipment

### GET /api/v2/equipment

查询设备列表。

响应 200：

```json
{"items":[{"equipment_ref":"EQ-2024-001","equipment_name":"数控机床A","equipment_status":"OPERATIONAL","department":"生产部","location_code":"W1-F2-03"}],"total":3}
```

### GET /api/v2/equipment/:equipment_ref

查询设备详情。

### PATCH /api/v2/equipment/:equipment_ref

更新设备状态。SCRAPPED状态不可逆转。

请求：

```json
{"equipment_status":"UNDER_REPAIR"}
```

## Technicians

### GET /api/v2/technicians

查询技师列表。

响应 200：

```json
{"items":[{"technician_badge":"TECH-2001","technician_name":"张工","technician_status":"AVAILABLE","skill_level":3,"department":"维修部"}],"total":3}
```

### GET /api/v2/technicians/:technician_badge

查询技师详情。

### PATCH /api/v2/technicians/:technician_badge

更新技师状态。

请求：

```json
{"technician_status":"ON_LEAVE"}
```

## Spare Parts

### POST /api/v2/tickets/:ticket_ref/parts

记录备件消耗。前置条件：ticket_status为IN_PROGRESS或PENDING_PARTS。

请求：

```json
{"part_code":"BRG-6205","consumed_qty":2,"unit_price":45.00}
```

响应 201：

```json
{"usage_ref":"USG-001","ticket_ref":"TK-20240101-001","part_code":"BRG-6205","consumed_qty":2,"unit_price":45.00,"line_cost":90.00}
```

业务规则：
- consumed_qty必须>0
- 同一ticket_ref同一part_code只能记录一次

### GET /api/v2/tickets/:ticket_ref/parts

查询工单备件消耗。

响应 200：

```json
{"items":[{"usage_ref":"USG-001","part_code":"BRG-6205","consumed_qty":2,"unit_price":45.00,"line_cost":90.00}],"parts_cost_total":90.00}
```

## Settlement

### POST /api/v2/tickets/:ticket_ref/settlement

创建结算。前置条件：ticket_status必须为COMPLETED。

请求：

```json
{"hourly_rate":80.00}
```

响应 201：

```json
{"settlement_ref":"STL-001","ticket_ref":"TK-20240101-001","labor_hours":3.5,"hourly_rate":80.00,"labor_cost":280.00,"parts_cost":90.00,"total_charge":370.00,"settlement_status":"PENDING_APPROVAL"}
```

业务规则：
- labor_cost = labor_hours * hourly_rate
- parts_cost = 备件line_cost之和
- total_charge = labor_cost + parts_cost

### GET /api/v2/tickets/:ticket_ref/settlement

查询结算。

### POST /api/v2/tickets/:ticket_ref/settlement/approve

审批结算。前置条件：settlement_status必须为PENDING_APPROVAL。

响应 200：

```json
{"settlement_status":"APPROVED","approved_by":"SUP-3001"}
```

## Health

### GET /api/v2/health

健康检查。

响应 200：

```json
{"status":"ok","service":"equipment-maintenance-mock"}
```
