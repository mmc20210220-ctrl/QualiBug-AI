# 分析器 Phase92A 集成指南

## 概述

本指南描述了如何将 8 个增强分析器集成到 QualiBug 的 Phase92A 证据管道中。

## 架构

### 现有架构
```
11 LLM Reasoning Engines
    ↓
Stage 2 (stage_reason_all_v2)
    ↓
Phase92A Evidence Pipeline
    ↓
Runtime Evidence Gate → Business Evidence Gate → Final Review
```

### 新增架构
```
11 LLM Reasoning Engines + 8 Local Analyzers
    ↓
Stage 2 (stage_reason_all_v2)
    ↓
Phase92A Evidence Pipeline
    ↓
Runtime Evidence Gate → Business Evidence Gate → Final Review
```

## 文件结构

### 新增文件
- `ai_test_asset_center/analyzers_adapter.py` - 分析器适配器模块
- `test_analyzers_integration.py` - 集成测试脚本

### 修改文件
- `ai_test_asset_center/stage_reason_all_v2.py` - 集成分析器到 Reasoner 阶段

## 功能特性

### 1. 分析器适配器 (analyzers_adapter.py)
- **AnalyzersAdapter 类**：统一管理所有 8 个分析器
- **build_analyzer_hypotheses()**：运行所有分析器并生成标准假设
- **get_analyzer_engine_names()**：获取所有可用分析器名称

### 2. 集成到 Stage 2
- 自动在 LLM Reasoner 之后运行分析器
- 分析器生成的假设完全符合 Phase92A 标准格式
- 支持通过环境变量 `QUALIBUG_USE_ANALYZERS` 控制开关

### 3. 分析器列表
1. **business_rules** - 业务规则分析
2. **state_machine** - 状态机分析
3. **multi_tenant** - 多租户隔离分析
4. **conservation** - 守恒规则分析
5. **concurrency** - 并发与竞态分析
6. **async_task** - 异步任务分析
7. **cache_consistency** - 缓存一致性分析
8. **authorization** - 认证授权分析

## 使用方法

### 启用/禁用分析器
```bash
# 启用分析器（默认）
$env:QUALIBUG_USE_ANALYZERS = "1"

# 禁用分析器
$env:QUALIBUG_USE_ANALYZERS = "0"
```

### 测试集成
```bash
python test_analyzers_integration.py
```

### 在代码中使用
分析器会自动在 `discovery_engine.py` 的 `stage_reason_all` 阶段运行，无需额外代码修改。

## 测试结果
```
Test 1: Import analyzers adapter                    [OK]
Test 2: Get analyzer engine names                   [OK]
Test 3: Initialize analyzers adapter                [OK]
Test 4: Build analyzer hypotheses                   [OK]
```

## 优势

1. **无缝集成**：分析器完全集成到现有 Phase92A 管道
2. **向后兼容**：可通过环境变量开关，默认启用
3. **证据追溯**：分析器生成的假设通过完整的证据门验证
4. **零风险**：完全向后兼容，不会破坏现有功能
5. **增强发现**：增加了 8 个专用分析器，覆盖更多 Bug 类型
