#!/usr/bin/env python
"""
QualiBug AI - 性能监控使用示例
安全地监控发现引擎，不修改核心代码
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

print("=" * 60)
print("QualiBug AI - 性能监控示例")
print("=" * 60)
print()

try:
    from ai_test_asset_center.performance_monitor import (
        measure_time,
        safe_exception_logger,
        PerformanceMetrics,
        get_performance_summary
    )
    print("[OK] 性能监控模块导入成功")
except ImportError as e:
    print(f"[ERROR] 导入失败: {e}")
    sys.exit(1)

print()

# ============================================================
# 示例 1: 基本用法 - 装饰器使用
# ============================================================
print("示例 1: 基本装饰器使用")
print("-" * 60)

@measure_time("example_function")
@safe_exception_logger("example_function")
def example_function(n: int) -> int:
    """一个示例函数"""
    import time
    total = 0
    for i in range(n):
        total += i
        time.sleep(0.01)  # 模拟耗时操作
    return total

result = example_function(10)
print(f"函数执行结果: {result}")
print()

# ============================================================
# 示例 2: 性能指标查看
# ============================================================
print("示例 2: 性能指标")
print("-" * 60)
print(get_performance_summary())
print()

# ============================================================
# 示例 3: 多次调用统计
# ============================================================
print("示例 3: 多次调用统计")
print("-" * 60)

@measure_time("multiple_calls")
def multiple_calls():
    for i in range(3):
        example_function(5)

multiple_calls()
print()
print(get_performance_summary())
print()

# ============================================================
# 示例 4: 模拟发现引擎的安全包装（不修改原代码）
# ============================================================
print("示例 4: 安全包装发现引擎（概念演示）")
print("-" * 60)

# 这里只是演示如何包装，不实际调用真实发现流程
print("""
如何包装发现引擎的方式：

class SafeMonitoredEngine(AutonomousDiscoveryEngine):
    
    @measure_time("stage_read")
    def stage_read(self, *args, **kwargs):
        return super().stage_read(*args, **kwargs)
""")
print()

# ============================================================
# 重置指标
# ============================================================
print("示例 5: 重置指标")
print("-" * 60)
PerformanceMetrics.reset()
print("指标已重置")
print("重置后的指标:", PerformanceMetrics.get_summary())
print()

print("=" * 60)
print("[OK] 示例运行完成！")
print("=" * 60)
print()
print("详细使用指南请查看: docs/OPTIMIZATION_GUIDE.md")
print()

