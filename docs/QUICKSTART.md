# QualiBug AI - 优化快速开始指南

## 5 分钟快速上手

### 步骤 1: 运行示例（推荐）

先看综合优化工具包的示例，了解所有功能：

```bash
python examples/example_optimizations.py
```

### 步骤 2: 一站式导入所有优化

```python
from ai_test_asset_center.optimizations import (
    # 综合装饰器
    optimized,
    optimized_network,
    optimized_api,
    optimized_cacheable,
    
    # 工具函数
    enable_all_optimizations,
    get_optimization_summary
)
```

### 步骤 3: 使用预设配置（最简单）

```python
# 网络请求优化
@optimized_network
def fetch_data():
    # ...

# API 调用优化
@optimized_api
def call_external_api():
    # ...

# 可缓存操作优化
@optimized_cacheable(ttl=300.0)
def expensive_calculation():
    # ...
```

### 步骤 4: 自定义组合（灵活）

```python
# 一键组合所有优化
@optimized(
    measure=True,    # 启用性能监控
    cache=True,      # 启用缓存
    retry=True,      # 启用重试
    cache_ttl=300.0,
    retry_max=3
)
def my_function():
    # ...
```

---

## 发现引擎优化示例（最重要！）

### 方式 1: 创建包装类（推荐，零风险）

```python
from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
from ai_test_asset_center.optimizations import (
    optimized_cacheable,
    measure_time
)

class OptimizedDiscoveryEngine(AutonomousDiscoveryEngine):
    """优化后的发现引擎"""
    
    @optimized_cacheable(ttl=300.0, prefix="route_map")
    def _build_route_map(self):
        """缓存 route_map，避免重复请求 OpenAPI"""
        return super()._build_route_map()
    
    @measure_time("stage_read")
    def stage_read(self, *args, **kwargs):
        return super().stage_read(*args, **kwargs)
    
    @measure_time("stage_reason_all")
    def stage_reason_all(self, *args, **kwargs):
        return super().stage_reason_all(*args, **kwargs)
    
    @measure_time("stage_execute")
    def stage_execute(self, *args, **kwargs):
        return super().stage_execute(*args, **kwargs)

# 使用
enable_all_optimizations()
engine = OptimizedDiscoveryEngine()
# ... 正常使用 ...

# 查看优化效果
print(get_optimization_summary())
```

### 方式 2: 选择性优化单个方法

```python
# 只缓存 route_map
from ai_test_asset_center.optimizations import cached

AutonomousDiscoveryEngine._build_route_map = cached(
    ttl_seconds=300.0,
    key_prefix="route_map"
)(AutonomousDiscoveryEngine._build_route_map)
```

---

## 功能说明

| 功能 | 装饰器 | 说明 |
|------|--------|------|
| 性能监控 | `@measure_time` | 测量函数执行时间 |
| 异常日志 | `@safe_exception_logger` | 安全记录异常 |
| 缓存 | `@cached` | 内存缓存，TTL 过期 |
| 重试 | `@safe_retry` | 指数退避重试 |

| 预设配置 | 说明 |
|----------|------|
| `@optimized_network` | 网络请求优化（监控 + 重试） |
| `@optimized_api` | API 调用优化（监控 + 重试） |
| `@optimized_cacheable` | 可缓存操作优化（监控 + 缓存） |
| `@optimized(...)` | 自定义组合所有优化 |

---

## 工具函数

| 函数 | 说明 |
|------|------|
| `enable_all_optimizations()` | 启用所有优化 |
| `disable_all_optimizations()` | 禁用所有优化 |
| `clear_all_caches()` | 清空所有缓存 |
| `reset_all_metrics()` | 重置所有性能指标 |
| `get_optimization_summary()` | 获取优化摘要 |

---

## 回滚计划

如果遇到问题，立即回滚：

```bash
# 删除所有新增文件
rm ai_test_asset_center/performance_monitor.py
rm ai_test_asset_center/safe_cache.py
rm ai_test_asset_center/safe_retry.py
rm ai_test_asset_center/optimizations.py
rm docs/OPTIMIZATION_GUIDE.md
rm docs/QUICKSTART.md
rm examples/example_*.py

# 或使用 git 回滚
git reset HEAD~5 --hard
```

---

## 详细文档

查看完整文档：`docs/OPTIMIZATION_GUIDE.md`

---

## 核心原则

[OK] **零风险** - 不修改核心业务逻辑
[OK] **向后兼容** - 不破坏现有功能
[OK] **可选使用** - 通过装饰器添加
[OK] **可随时移除** - 删除新文件即可回滚

