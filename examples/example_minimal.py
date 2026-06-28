#!/usr/bin/env python
"""
QualiBug AI - 最小化示例
展示如何在 1 分钟内应用优化到实际项目
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

print("=" * 60)
print("QualiBug AI - 最小化示例")
print("=" * 60)

# ============================================================
# 方式 1: 最简单 - 只缓存 route_map
# ============================================================

print("\n方式 1: 最简单 - 只缓存 route_map")
print("-" * 60)

try:
    from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine
    from ai_test_asset_center.optimizations import cached, enable_all_optimizations
    
    # 启用优化
    enable_all_optimizations()
    
    # 只缓存 route_map（最大的性能提升！）
    AutonomousDiscoveryEngine._build_route_map = cached(
        ttl_seconds=300.0,  # 缓存 5 分钟
        key_prefix="discovery_route_map"
    )(AutonomousDiscoveryEngine._build_route_map)
    
    print("[OK] route_map 缓存已启用！")
    
except ImportError as e:
    print(f"[WARN] 注意: {e}")
    print("这只是一个示例，发现引擎可能不在当前环境中")

# ============================================================
# 方式 2: 创建优化后的子类（推荐）
# ============================================================

print("\n方式 2: 创建优化后的子类（推荐）")
print("-" * 60)

try:
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
    
    print("[OK] OptimizedDiscoveryEngine 已创建！")
    print("使用方法：engine = OptimizedDiscoveryEngine()")
    
except ImportError as e:
    print(f"[WARN] 注意: {e}")
    print("这只是一个示例，发现引擎可能不在当前环境中")

# ============================================================
# 方式 3: 使用综合装饰器
# ============================================================

print("\n方式 3: 使用综合装饰器")
print("-" * 60)

from ai_test_asset_center.optimizations import optimized, get_optimization_summary

# 假设我们有一个不稳定的函数
flaky_call_count = 0

@optimized(
    measure=True,    # 性能监控
    cache=True,      # 缓存
    retry=True,      # 重试
    cache_ttl=60.0,
    retry_max=3,
    name="my_important_function"
)
def my_important_function(x: int) -> int:
    """一个重要但不稳定的函数"""
    global flaky_call_count
    flaky_call_count += 1
    
    # 模拟前 2 次失败
    if flaky_call_count < 3:
        print(f"  尝试 {flaky_call_count} - 模拟失败")
        raise RuntimeError(f"失败 #{flaky_call_count}")
    
    print(f"  尝试 {flaky_call_count} - 成功！输入: {x}")
    return x * 2

# 测试
print("\n第一次调用（会重试）:")
result = my_important_function(10)
print(f"结果: {result}")

print("\n第二次调用（缓存命中）:")
result = my_important_function(10)
print(f"结果: {result}")

print("\n优化摘要:")
print(get_optimization_summary())

# ============================================================
# 完成
# ============================================================

print("\n" + "=" * 60)
print("示例完成！")
print("=" * 60)
print("\n下一步：")
print("1. 复制适合你项目的方式到你的代码中")
print("2. 运行示例测试优化效果")
print("3. 查看 docs/QUICKSTART.md 了解更多")

