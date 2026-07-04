# QualiBug Enterprise Upgrade Plan

## 改动文件清单

### 1. discovery_engine.py — 核心引擎修复
- _build_route_map() 支持注入内存OpenAPI（不再依赖目标服务器）
- _resolve_call() 增强模糊匹配
- TestDataGenerator 从API响应获取真实ID
- MAX_HYPOTHESES_EXECUTE=2000
- Sandbox写操作放开

### 2. autonomous_pipeline.py — 管道接线
- ProjectContextBuilder 统一上下文构建
- 上下文增强 (Requirement=45000, Api=50000, DB=25000, BugHistory=25000)
- 发现引擎证据保留(verdict/method/path→标准格式)
- RiskCluePool 保存未确证线索

### 3. stage_reason_all_v2.py — 推理引擎扩容
- MAX_HYPOTHESES=300 (最大500)
- 清理[:15]硬编码
- 上下文截断提升

### 4. bug_validation_queue.py — 验证队列
- VALIDATION_QUEUE_LIMIT=1000
- Sandbox写操作增强(before/after/rollback)
- EnvironmentClassifier集成

### 5. adaptive_probe_optimizer.py — Oracle增强
- MoneyOracle/InventoryOracle/PermissionOracle/WorkflowOracle/IdempotentOracle/ConsistencyOracle

## 配置项(环境变量)
- QUALIBUG_REASONER_MAX_HYPOTHESES=300
- QUALIBUG_MAX_HYPOTHESES_EXECUTE=2000
- QUALIBUG_VALIDATION_QUEUE_LIMIT=1000
- QUALIBUG_ALLOW_UNAUTH_WRITE_PROBES=1 (sandbox)
- QUALIBUG_REQUIREMENT_CONTEXT_CHARS=45000
- QUALIBUG_API_CONTEXT_CHARS=50000
- QUALIBUG_DATABASE_CONTEXT_CHARS=25000
- QUALIBUG_BUG_HISTORY_CONTEXT_CHARS=25000

## 不改动的模块
- 17个Reasoner引擎（不重构）
- CLI/API接口（保持兼容）
- 前端（不受影响）
