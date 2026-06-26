# QualiBug AI API Documentation

**Version**: Phase92A  
**Base URL**: `http://localhost:5000`

---

## 认证

当前版本使用基于角色的访问控制，通过请求头传递：

```
X-Actor: admin|analyst|viewer
```

---

## API 端点

### 健康检查

```
GET /api/health
```

**响应**:
```json
{
  "status": "healthy",
  "version": "92.0.0",
  "phase": "phase92a"
}
```

---

### 服务状态

```
GET /api/pilot/status
```

**响应**:
```json
{
  "project": "default",
  "environment": "development",
  "llm_status": "connected",
  "findings_count": 5,
  "last_scan": "2026-06-25T12:00:00Z"
}
```

---

### 运行扫描

```
POST /api/scan/run
```

**请求体**:
```json
{
  "project_id": "myproject",
  "prd_content": "产品需求文档内容...",
  "api_spec": "OpenAPI规范内容..."
}
```

**响应**:
```json
{
  "ok": true,
  "message": "扫描完成，发现 3 个 Bug",
  "findings": [
    {
      "id": "FIND-001",
      "title": "订单金额守恒违规",
      "severity": "P1",
      "verdict": "needs_more_evidence",
      "finding_gate": {
        "verdict": "NEEDS_MORE_EVIDENCE",
        "runtime_gate_status": "PASSED",
        "business_gate_status": "PENDING",
        "missing": ["CLEANUP_PENDING"]
      }
    }
  ]
}
```

---

### 知识导入

```
POST /api/knowledge/ingest
```

**请求体**:
```json
{
  "project_id": "myproject",
  "source_type": "openapi|prd|database",
  "content": "...",
  "metadata": {}
}
```

---

### 配置管理

```
GET /api/pilot/config
```

**响应**:
```json
{
  "project": "myproject",
  "llm": {
    "provider": "openai",
    "model": "gpt-4",
    "status": "connected"
  },
  "database": {
    "type": "sqlite",
    "path": "data/mes_buglab.db"
  }
}
```

```
POST /api/pilot/config
```

**请求体**:
```json
{
  "project_id": "myproject",
  "payload": {
    "llm_provider": "openai",
    "llm_model": "gpt-4"
  }
}
```

---

### 发现列表

```
GET /api/findings
```

**查询参数**:
- `project_id`: 项目ID
- `status`: 状态筛选
- `severity`: 严重程度筛选

**响应**:
```json
{
  "findings": [
    {
      "id": "FIND-001",
      "title": "订单金额守恒违规",
      "severity": "P1",
      "verdict": "validated_candidate",
      "created_at": "2026-06-25T12:00:00Z",
      "evidence": {
        "has_snapshot": true,
        "has_entity_binding": true,
        "cleanup_status": "CLEAN"
      }
    }
  ],
  "total": 1
}
```

---

### 发现详情

```
GET /api/findings/{finding_id}
```

**响应**:
```json
{
  "id": "FIND-001",
  "title": "订单金额守恒违规",
  "severity": "P1",
  "verdict": "validated_candidate",
  "raw_runtime_verdict": "confirmed",
  "semantic_verdict": "SEMANTIC_CONFIRMED",
  "business_evidence_status": "VALIDATED",
  "final_review_status": "PENDING_REVIEW",
  "evidence": {
    "calls": [...],
    "entity_binding": {...},
    "before_snapshot_ref": "snap:before:abc123",
    "after_snapshot_ref": "snap:after:def456",
    "cleanup": {"status": "CLEAN"}
  },
  "finding_gate": {
    "verdict": "VALIDATED_CANDIDATE",
    "runtime_gate_status": "PASSED",
    "business_gate_status": "PASSED"
  }
}
```

---

### 人工审核

```
POST /api/findings/{finding_id}/review
```

**请求体**:
```json
{
  "action": "confirm|reject|needs_info",
  "comment": "审核意见",
  "reviewer": "analyst@company.com"
}
```

---

### 导出快照

```
GET /api/export/snapshot
```

**响应**: JSON文件下载

---

## 错误响应

```json
{
  "ok": false,
  "error": "ERROR_CODE",
  "message": "错误描述"
}
```

**常见错误码**:
- `NOT_FOUND`: 资源不存在
- `PERMISSION_DENIED`: 权限不足
- `VALIDATION_ERROR`: 数据验证失败
- `SCAN_FAILED`: 扫描失败
- `LLM_ERROR`: LLM调用失败

---

## 四层状态

Phase92A 引入四层状态保留：

| 层级 | 字段 | 说明 |
|------|------|------|
| Layer 1 | `raw_runtime_verdict` | 原始运行时裁决 |
| Layer 2 | `semantic_verdict` | 语义裁决 |
| Layer 3 | `business_evidence_status` | 业务证据状态 |
| Layer 4 | `final_review_status` | 最终审核状态 |

**状态流转**:
```
inconclusive → confirmed → SEMANTIC_CONFIRMED → VALIDATED → PENDING_REVIEW
                          ↘ SEMANTIC_FALSIFIED → REJECTED
                          ↘ SEMANTIC_PENDING → NEEDS_MORE_EVIDENCE
```

---

## 门控状态

### Runtime Gate

| 状态 | 说明 |
|------|------|
| `PASSED` | 运行时证据完整 |
| `FAILED_MISSING_CALLS` | 缺少探针调用 |
| `FAILED_NO_RAW_EVIDENCE` | 无原始证据引用 |

### Business Gate

| 状态 | 说明 |
|------|------|
| `PASSED` | 业务证据完整 |
| `PENDING` | 等待补充证据 |
| `ENTITY_BINDING_MISSING` | 缺少实体绑定 |
| `SNAPSHOTS_MISSING` | 缺少快照 |
| `CLEANUP_PENDING` | Cleanup未完成 |

---

## 版本历史

- **Phase92A**: 证据桥接与双层门控
- **Phase91**: 认知记忆图与风险前沿
- **Phase90**: 企业安全增强