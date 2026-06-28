#!/usr/bin/env python
"""
QualiBug AI - 综合优化示例
展示如何组合使用性能监控和缓存
"""

from __future__ import annotations

import sys
from pathlib import Path
import time

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

print("=" * 60)
print("QualiBug AI - 综合优化示例")
print("=" * 60)
print()

# ============================================================
# 导入优化模块
# ============================================================
try:
    from ai_test_asset_center.performance_monitor import (
        measure_time,
        PerformanceMetrics,
        get_performance_summary
    )
    from ai_test_asset_center.safe_cache import (
        cached,
        SafeCache,
        get_cache_stats,
        clear_cache,
        enable_cache,
        disable_cache
    )
    print("[OK] 优化模块导入成功")
except ImportError as e:
    print(f"[ERROR] 导入失败: {e}")
    sys.exit(1)

print()

# ============================================================
# 示例 1: 模拟 _build_route_map 的缓存效果
# ============================================================
print("示例 1: 缓存优化 - 模拟 route_map 构建")
print("-" * 60)

# 模拟一个耗时的函数（类似 _build_route_map）
def slow_build_route_map() -> dict:
    """模拟构建 route_map 的耗时操作"""
    print("  [BUILD] 正在构建 route_map（模拟耗时操作）...")
    time.sleep(0.5)  # 模拟网络请求延迟
    return {
        "GET /api/materials": {"path": "/api/materials"},
        "POST /api/materials": {"path": "/api/materials"},
    }

# 包装带缓存的版本
@cached(ttl_seconds=10.0, key_prefix="route_map_example")
def cached_build_route_map() -> dict:
    return slow_build_route_map()

# 测试缓存效果
print("\n第一次调用（应该会执行）:")
start = time.time()
result1 = cached_build_route_map()
print(f"  耗时: {time.time() - start:.3f}s")

print("\n第二次调用（应该从缓存获取）:")
start = time.time()
result2 = cached_build_route_map()
print(f"  耗时: {time.time() - start:.3f}s")

print(f"\n结果一致: {result1 == result2}")
print(f"缓存统计: {get_cache_stats()}")
print()

# ============================================================
# 示例 2: 组合使用缓存和性能监控
# ============================================================
print("示例 2: 组合优化 - 缓存 + 性能监控")
print("-" * 60)

@measure_time("combined_function")
@cached(ttl_seconds=5.0, key_prefix="combined_example")
def combined_function(n: int) -> int:
    """同时使用缓存和监控"""
    print(f"  [EXEC] 执行函数，参数 n={n}")
    total = 0
    for i in range(n):
        total += i
        time.sleep(0.01)
    return total

# 测试组合效果
print("\n第一次调用 n=10:")
result_a = combined_function(10)

print("\n第二次调用 n=10（缓存命中）:")
result_b = combined_function(10)

print("\n第三次调用 n=20（不同参数，缓存未命中）:")
result_c = combined_function(20)

print("\n性能摘要:")
print(get_performance_summary())
print()

# ============================================================
# 示例 3: 缓存开关演示
# ============================================================
print("示例 3: 缓存开关演示")
print("-" * 60)

@cached(ttl_seconds=5.0, key_prefix="switch_example")
def switch_test_function(x: int) -> int:
    print(f"  [EXEC] 执行函数 x={x}")
    return x * 2

print("\n缓存启用状态:")
enable_cache()
print(f"  cache enabled: {SafeCache.is_enabled()}")
switch_test_function(5)  # 执行
switch_test_function(5)  # 缓存命中，不打印

print("\n禁用缓存:")
disable_cache()
print(f"  cache enabled: {SafeCache.is_enabled()}")
switch_test_function(5)  # 每次都执行
switch_test_function(5)  # 每次都执行

print("\n重新启用缓存:")
enable_cache()
clear_cache()  # 清空缓存
print(f"  cache enabled: {SafeCache.is_enabled()}")
print()

# ============================================================
# 示例 4: 如何安全包装发现引擎（概念）
# ============================================================
print("示例 4: 安全包装发现引擎（概念演示）")
print("-" * 60)
print("""
如何在不修改原代码的情况下优化：

from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
from ai_test_asset_center.safe_cache import cached, enable_cache
from ai_test_asset_center.performance_monitor import measure_time

class OptimizedDiscoveryEngine(AutonomousDiscoveryEngine):
    
    @measure_time("build_route_map")
    @cached(ttl_seconds=300.0, key_prefix="discovery_route_map")
    def _build_route_map(self):
        return super()._build_route_map()
    
    @measure_time("stage_read")
    def stage_read(self, *args, **kwargs):
        return super().stage_read(*args, **kwargs)

# 使用
enable_cache()
engine = OptimizedDiscoveryEngine()
# ... 正常使用 ...
""")
print()

# ============================================================
# 清理
# ============================================================
clear_cache()
PerformanceMetrics.reset()

print("=" * 60)
print("[OK] 综合示例运行完成！")
print("=" * 60)
print()
print("更多详情请查看: docs/OPTIMIZATION_GUIDE.md")
print()

