# QualiBug AI - 安全优化指南

## 概述

本文档介绍如何**安全地**优化 QualiBug AI 的发现引擎，不修改核心业务逻辑，只添加可观察性。

---

## 新增模块：`performance_monitor.py`

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

## 回滚计划

如果遇到问题，立即回滚：

1. **删除新文件**：
   - `ai_test_asset_center/performance_monitor.py`
   - `docs/OPTIMIZATION_GUIDE.md`

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

