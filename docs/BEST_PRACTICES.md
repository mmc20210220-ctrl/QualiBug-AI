# QualiBug AI - 优化最佳实践

## 目录

1. [概述](#概述)
2. [推荐优化方案](#推荐优化方案)
3. [性能优化进阶](#性能优化进阶)
4. [生产环境建议](#生产环境建议)
5. [常见问题](#常见问题)

---

## 概述

本文档提供优化工具的最佳实践和高级用法，帮助你最大化利用这些优化工具。

---

## 推荐优化方案

### 方案 1: 最小改动（推荐）

**最安全，零风险**

```python
from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
from ai_test_asset_center.optimizations import (
    optimized_cacheable,
    measure_time,
    enable_all_optimizations
)

class OptimizedDiscoveryEngine(AutonomousDiscoveryEngine):
    """优化后的发现引擎 - 继承式优化"""
    
    @optimized_cacheable(ttl=300.0, prefix="route_map")
    def _build_route_map(self):
        """缓存 route_map（最大性能提升）"""
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
    
    @measure_time("stage_verify")
    def stage_verify(self, *args, **kwargs):
        return super().stage_verify(*args, **kwargs)

# 使用
enable_all_optimizations()
engine = OptimizedDiscoveryEngine()
```

### 方案 2: 全功能优化

```python
from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
from ai_test_asset_center.optimizations import (
    optimized,
    optimized_network,
    enable_cache,
    get_optimization_summary
)

class FullyOptimizedDiscoveryEngine(AutonomousDiscoveryEngine):
    """全功能优化的发现引擎"""
    
    @optimized(
        measure=True,
        cache=True,
        retry=True,
        cache_ttl=300.0,
        retry_max=3,
        name="build_route_map"
    )
    def _build_route_map(self):
        return super()._build_route_map()
    
    @optimized_network
    def _http(self, *args, **kwargs):
        return super()._http(*args, **kwargs)

# 使用
enable_cache()
engine = FullyOptimizedDiscoveryEngine()
```

---

## 性能优化进阶

### 1. 缓存策略建议

| 场景 | 推荐 TTL | 说明 |
|------|----------|------|
| OpenAPI spec | 300s (5 分钟) | 不经常变化 |
| 路由映射 | 300s (5 分钟) | 同上 |
| 业务配置 | 600s (10 分钟) | 可能变化但不频繁 |
| 静态数据 | 1800s (30 分钟) | 几乎不变化 |

### 2. 重试策略建议

```python
from ai_test_asset_center.safe_retry import safe_retry

# 网络请求 - 较长的退避
@safe_retry(
    max_retries=3,
    initial_delay=1.0,
    max_delay=10.0,
    backoff_factor=2.0,
    name="network_request"
)
def make_network_request():
    # ...

# API 调用 - 较短的退避
@safe_retry(
    max_retries=2,
    initial_delay=0.5,
    max_delay=5.0,
    backoff_factor=1.5,
    name="api_call"
)
def call_external_api():
    # ...
```

### 3. 性能监控建议

```python
from ai_test_asset_center.performance_monitor import (
    PerformanceMetrics,
    get_performance_summary
)

# 定期记录性能摘要
def log_performance_stats():
    summary = get_performance_summary()
    if summary:
        print("=" * 80)
        print("性能摘要")
        print("=" * 80)
        print(summary)
        print()

# 定期清理旧指标
def reset_metrics_if_needed():
    stats = PerformanceMetrics.get_stats()
    if stats and stats.get("count", 0) > 1000:
        PerformanceMetrics.reset()
```

---

## 生产环境建议

### 1. 渐进式启用

```python
import os
from ai_test_asset_center.optimizations import (
    enable_cache,
    disable_cache
)

# 根据环境变量决定是否启用
if os.environ.get("ENABLE_OPTIMIZATIONS") == "true":
    enable_cache()
    print("[OK] 优化已启用")
else:
    disable_cache()
    print("[INFO] 优化未启用")
```

### 2. 监控和告警

```python
from ai_test_asset_center.optimizations import get_optimization_summary

def check_performance_thresholds():
    """检查性能指标"""
    summary = get_optimization_summary()
    
    # 检查缓存命中率
    cache_stats = ...
    if cache_stats and cache_stats.get("hit_rate", 0) < 0.5:
        print("[WARN] 缓存命中率过低")
    
    # 检查慢操作
    metrics = ...
    for metric_name, metric_data in metrics.items():
        avg_time = metric_data.get("avg", 0)
        if avg_time > 10.0:  # 超过 10 秒
            print(f"[WARN] {metric_name} 平均执行时间过长: {avg_time:.2f}s")
```

### 3. 定期清理

```python
from ai_test_asset_center.optimizations import (
    clear_all_caches,
    reset_all_metrics
)

def periodic_cleanup():
    """定期清理"""
    clear_all_caches()
    reset_all_metrics()
    print("[OK] 缓存和指标已清理")
```

---

## 常见问题

### Q1: 这些优化会影响原功能吗？

A: **不会！** 所有优化都是：
- 零风险 - 不修改核心业务逻辑
- 可选使用 - 通过装饰器添加
- 可随时移除 - 删除新文件即可回滚

### Q2: 如何选择哪些方法需要优化？

A: 优先优化：
1. **网络请求** - 添加重试
2. **OpenAPI spec 请求** - 添加缓存
3. **耗时较长的阶段** - 添加性能监控
4. **不稳定的操作** - 添加重试

### Q3: 缓存会导致数据过期吗？

A: 是的，所以需要合理设置 TTL：
- 不经常变化的数据 - 5-10 分钟
- 可能变化的数据 - 1-2 分钟
- 使用 `disable_cache()` 可以临时禁用

### Q4: 如何在生产环境验证优化效果？

A: 可以这样：
```python
from ai_test_asset_center.optimizations import get_optimization_summary

# 运行优化前后对比
print("=" * 80)
print("优化前性能")
print("=" * 80)
# ... 运行流程 ...

# 启用优化
enable_all_optimizations()

print("=" * 80)
print("优化后性能")
print("=" * 80)
# ... 再次运行流程 ...
print(get_optimization_summary())
```

### Q5: 遇到问题如何回滚？

A: 简单回滚：
```bash
# 删除所有新增文件
rm ai_test_asset_center/performance_monitor.py
rm ai_test_asset_center/safe_cache.py
rm ai_test_asset_center/safe_retry.py
rm ai_test_asset_center/optimizations.py
rm docs/OPTIMIZATION_GUIDE.md
rm docs/QUICKSTART.md
rm docs/BEST_PRACTICES.md
rm examples/example_*.py

# 或使用 git 回滚
git reset HEAD~6 --hard
```

---

## 总结

使用这些优化工具的最佳实践：

1. **从简单开始** - 先运行 `examples/example_minimal.py`
2. **渐进式启用** - 先启用缓存，再添加监控，最后添加重试
3. **监控效果** - 使用 `get_optimization_summary()` 定期检查
4. **保持安全** - 永远不修改核心代码，只通过继承和装饰器优化
5. **按需调整** - 根据实际情况调整 TTL、重试次数等参数

---

**记住：安全第一，性能第二！** 🎯

