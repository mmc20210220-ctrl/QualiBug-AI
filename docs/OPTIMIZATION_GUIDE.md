# QualiBug AI - 安全优化指南

## 概述

本文档介绍如何**安全地**优化 QualiBug AI 的发现引擎，不修改核心业务逻辑，只添加可观察性和缓存优化。

## 快速开始 - 示例文件

想要立即开始？查看这些示例文件：

| 文件 | 说明 |
|------|------|
| `examples/example_optimizations.py` | **强烈推荐** - 综合优化工具包示例 |
| `examples/example_monitor_usage.py` | 性能监控基本用法 |
| `examples/example_combined_optimizations.py` | 缓存 + 监控组合使用 |
| `examples/integrated_optimization_demo.py` | 完整集成演示 |
| `examples/example_safe_retry_simple.py` | 安全重试装饰器示例 |

运行示例：
```bash
# 综合优化工具包（推荐）
python examples/example_optimizations.py

# 其他示例
python examples/integrated_optimization_demo.py
python examples/example_safe_retry_simple.py
```

---

## 新增模块

### 1. `performance_monitor.py` - 性能监控

### 功能
- ✅ 性能监控装饰器
- ✅ 异常日志装饰器
- ✅ 性能指标收集
- ✅ 零风险（不改变现有代码行为）

### 特点
- **向后兼容**：完全不改变现有代码功能
- **可选使用**：只在需要时添加装饰器
- **可随时移除**：移除装饰器不影响功能

---

## 快速开始

### 1. 基本用法 - 不修改现有代码

创建一个独立的脚本，包装现有功能：

```python
# optimized_discovery.py - 独立的优化包装脚本
from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
from ai_test_asset_center.performance_monitor import (
    measure_time,
    safe_exception_logger,
    get_performance_summary
)

# 继承并装饰，不修改原代码
class MonitoredDiscoveryEngine(AutonomousDiscoveryEngine):
    
    @measure_time("stage_read")
    @safe_exception_logger("stage_read")
    def stage_read(self, *args, **kwargs):
        return super().stage_read(*args, **kwargs)
    
    @measure_time("stage_execute")
    def stage_execute(self, *args, **kwargs):
        return super().stage_execute(*args, **kwargs)

# 使用
engine = MonitoredDiscoveryEngine()
# ... 正常使用 ...
print(get_performance_summary())
```

### 2. 在现有代码中安全使用（可选）

如果确实想修改原代码（谨慎！），可以这样：

```python
# 在 discovery_engine.py 顶部添加
try:
    from .performance_monitor import measure_time, safe_exception_logger
    _HAS_MONITOR = True
except ImportError:
    _HAS_MONITOR = False

# 然后装饰方法（保持原有逻辑不变）
def _login(self):
    # ... 原有代码 ...

if _HAS_MONITOR:
    _login = safe_exception_logger("discovery_engine._login")(_login)
```

---

## 装饰器 API

### `@measure_time(name=None, log_level=logging.INFO, collect_metrics=True)`

测量方法执行时间。

```python
@measure_time("my_function")
def my_function():
    # ...
```

### `@safe_exception_logger(name=None, reraise=True, log_level=logging.WARNING)`

记录异常但**保持原有行为**（默认重新抛出异常）。

```python
@safe_exception_logger("risky_function")
def risky_function():
    # ...
```

---

## 性能指标

### 获取摘要
```python
from ai_test_asset_center.performance_monitor import (
    PerformanceMetrics,
    get_performance_summary
)

print(get_performance_summary())
```

输出示例：
```
============================================================
性能摘要
============================================================
stage_read                               | 次数:   5 | 平均: 2.345s | 最小: 1.234s | 最大: 3.456s | 总计: 11.725s
stage_execute                            | 次数:  10 | 平均: 0.567s | 最小: 0.123s | 最大: 1.234s | 总计:  5.670s
============================================================
```

### 重置指标
```python
PerformanceMetrics.reset()  # 重置所有
PerformanceMetrics.reset("stage_read")  # 只重置特定指标
```

---

### 2. `safe_cache.py` - 安全缓存模块

#### 功能
- ✅ 内存缓存装饰器
- ✅ TTL 过期机制
- ✅ 可随时开关
- ✅ 零风险（不改变现有代码行为）

#### 特点
- **向后兼容**：完全不改变现有代码功能
- **可选使用**：通过装饰器选择性添加
- **可随时禁用**：`SafeCache.disable()` 即可完全关闭
- **性能提升**：`_build_route_map` 等重复调用可缓存

#### 快速使用

```python
from ai_test_asset_center.safe_cache import (
    cached,
    SafeCache,
    enable_cache,
    get_cache_stats
)

# 装饰需要缓存的函数
@cached(ttl_seconds=300.0, key_prefix="route_map")
def build_route_map():
    # ... 原代码 ...

# 启用缓存
enable_cache()
```

#### 缓存 API

##### `@cached(ttl_seconds=None, key_prefix="")`
缓存函数返回值

```python
@cached(ttl_seconds=300.0, key_prefix="my_data")
def expensive_function():
    # ... 耗时操作 ...
```

##### 缓存控制
```python
from ai_test_asset_center.safe_cache import (
    enable_cache,
    disable_cache,
    clear_cache,
    get_cache_stats
)

enable_cache()      # 启用缓存
disable_cache()     # 禁用缓存
clear_cache()       # 清空缓存
print(get_cache_stats())  # 查看缓存统计
```

---

### 3. `safe_retry.py` - 安全重试装饰器

#### 功能
- 安全的重试装饰器（零风险，不改变函数行为）
- 指数退避 + 抖动
- 可配置重试次数、延迟等参数
- 预设配置：网络请求、API 调用

#### 特点
- 零风险：只在失败时重试，不改变原函数逻辑
- 可选使用：装饰器方式添加
- 可随时移除

#### 快速使用

```python
from ai_test_asset_center.safe_retry import (
    safe_retry,
    safe_retry_network,
    safe_retry_api
)

# 基本重试
@safe_retry(max_retries=3, initial_delay=0.5)
def flaky_operation():
    # ... 可能失败的操作 ...

# 网络请求重试（预设配置）
@safe_retry_network
def fetch_from_server():
    # ... 网络请求 ...

# API 调用重试（预设配置）
@safe_retry_api
def call_external_api():
    # ... API 调用 ...
```

#### 完整配置选项

```python
@safe_retry(
    max_retries=3,              # 最大重试次数
    initial_delay=0.5,          # 初始延迟（秒）
    max_delay=10.0,             # 最大延迟（秒）
    backoff_factor=2.0,         # 退避因子
    jitter=0.1,                 # 抖动因子
    name="my_operation"         # 操作名称（用于日志）
)
def my_function():
    # ...
```

---

### 4. `optimizations.py` - 综合优化工具包 🚀

#### 功能
- 一站式导入所有优化
- `@optimized` - 综合装饰器，一键组合所有优化
- 预设配置：`@optimized_network`、`@optimized_api`、`@optimized_cacheable`
- 工具函数：启用/禁用、清空缓存、获取摘要

#### 特点
- 零风险，不修改现有代码
- 自动处理模块缺失的情况
- 开箱即用，预设配置

#### 快速使用

```python
# 一站式导入！
from ai_test_asset_center.optimizations import (
    optimized,
    optimized_network,
    optimized_api,
    optimized_cacheable,
    enable_all_optimizations,
    get_optimization_summary
)

# 一键组合所有优化
@optimized(
    measure=True,    # 性能监控
    cache=True,      # 缓存
    retry=True,      # 重试
    cache_ttl=300.0,
    retry_max=3
)
def my_function():
    # ...

# 使用预设配置
@optimized_network
def fetch_data():
    # ...

@optimized_cacheable(ttl=60.0)
def expensive_calc():
    # ...

# 工具函数
enable_all_optimizations()
print(get_optimization_summary())
```

#### 发现引擎优化示例

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
        """缓存 route_map"""
        return super()._build_route_map()
    
    @measure_time("stage_read")
    def stage_read(self, *args, **kwargs):
        return super().stage_read(*args, **kwargs)
    
    @measure_time("stage_reason_all")
    def stage_reason_all(self, *args, **kwargs):
        return super().stage_reason_all(*args, **kwargs)
```

---

## 安全优化建议

### 优先级 P0 - 零风险
1. **只使用装饰器**：不修改核心逻辑
2. **创建包装类**：继承而不是修改原类
3. **添加日志**：使用 safe_exception_logger 记录异常

### 优先级 P1 - 低风险
1. **添加 route_map 缓存**（可开关）
2. **添加进度回调**（已在代码中存在）
3. **优化日志输出**（不改变逻辑）

### 优先级 P2 - 需要测试
1. **并行执行假设**（需要全面测试）
2. **性能瓶颈优化**（需要基准测试）

---

## 完整示例：监控整个发现流程

```python
"""
示例：安全地监控发现引擎
不修改 discovery_engine.py 代码
"""

from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
from ai_test_asset_center.performance_monitor import (
    measure_time,
    safe_exception_logger,
    get_performance_summary,
    PerformanceMetrics
)
import logging

# 启用更详细的日志
logging.basicConfig(level=logging.INFO)

class SafeMonitoredEngine(AutonomousDiscoveryEngine):
    """安全的监控包装类 - 不修改父类逻辑"""
    
    @measure_time("SafeMonitoredEngine.stage_read")
    @safe_exception_logger("SafeMonitoredEngine.stage_read")
    def stage_read(self, *args, **kwargs):
        return super().stage_read(*args, **kwargs)
    
    @measure_time("SafeMonitoredEngine.stage_reason_all")
    def stage_reason_all(self, *args, **kwargs):
        return super().stage_reason_all(*args, **kwargs)
    
    @measure_time("SafeMonitoredEngine.stage_execute")
    def stage_execute(self, *args, **kwargs):
        return super().stage_execute(*args, **kwargs)
    
    @measure_time("SafeMonitoredEngine.stage_verify")
    def stage_verify(self, *args, **kwargs):
        return super().stage_verify(*args, **kwargs)

# 使用示例
def run_monitored_discovery():
    print("开始监控发现流程...")
    engine = SafeMonitoredEngine()
    
    # ... 正常执行你的发现流程 ...
    # result = engine.stage_read(...)
    # hypotheses = engine.stage_reason_all(...)
    # etc.
    
    # 显示性能摘要
    print("\n" + get_performance_summary())
    
    # 获取详细指标
    metrics = PerformanceMetrics.get_summary()
    print(f"\n总耗时: {sum(m['total'] for m in metrics.values()):.3f}s")

if __name__ == "__main__":
    run_monitored_discovery()
```

---

## 完整示例：组合优化（监控 + 缓存）

```python
"""
示例：组合使用性能监控和缓存优化
不修改 discovery_engine.py 代码
"""

from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
from ai_test_asset_center.performance_monitor import (
    measure_time,
    get_performance_summary,
    PerformanceMetrics
)
from ai_test_asset_center.safe_cache import (
    cached,
    enable_cache,
    get_cache_stats
)
import logging

# 启用缓存和日志
enable_cache()
logging.basicConfig(level=logging.INFO)

class FullyOptimizedEngine(AutonomousDiscoveryEngine):
    """组合优化的发现引擎 - 缓存 + 监控"""
    
    @measure_time("build_route_map")
    @cached(ttl_seconds=300.0, key_prefix="discovery_route_map")
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

# 使用示例
def run_optimized_discovery():
    print("开始优化后的发现流程...")
    engine = FullyOptimizedEngine()
    
    # ... 正常执行你的发现流程 ...
    
    # 显示性能摘要
    print("\n性能摘要:")
    print(get_performance_summary())
    
    # 显示缓存统计
    print("\n缓存统计:")
    print(get_cache_stats())

if __name__ == "__main__":
    run_optimized_discovery()
```

---

## 回滚计划

如果遇到问题，立即回滚：

1. **删除新文件**：
   - `ai_test_asset_center/performance_monitor.py`
   - `ai_test_asset_center/safe_cache.py`
   - `ai_test_asset_center/safe_retry.py`
   - `ai_test_asset_center/optimizations.py`
   - `docs/OPTIMIZATION_GUIDE.md`
   - `examples/example_monitor_usage.py`
   - `examples/example_combined_optimizations.py`
   - `examples/integrated_optimization_demo.py`
   - `examples/example_safe_retry_simple.py`
   - `examples/example_optimizations.py`

2. **恢复原代码**（如果修改过）：
   - 移除装饰器
   - 恢复原方法签名

3. **验证**：
   - 运行现有测试确保功能正常

---

## 下一步

- [ ] 先运行现有测试确保基线正常
- [ ] 使用包装类测试监控功能
- [ ] 收集性能数据找到瓶颈
- [ ] 基于数据再做针对性优化

