#!/usr/bin/env python
"""
QualiBug AI - 综合优化工具包示例
展示如何使用 optimizations.py 一站式优化
"""

from __future__ import annotations

import sys
from pathlib import Path
import time
import logging

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# ============================================================
# 一站式导入所有优化！
# ============================================================

from ai_test_asset_center.optimizations import (
    # 综合装饰器
    optimized,
    optimized_network,
    optimized_api,
    optimized_cacheable,
    
    # 工具函数
    enable_all_optimizations,
    disable_all_optimizations,
    clear_all_caches,
    reset_all_metrics,
    get_optimization_summary,
    
    # 也可以单独使用各模块
    measure_time,
    cached,
    safe_retry
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

print("=" * 60)
print("QualiBug AI - 综合优化工具包示例")
print("=" * 60)

# ============================================================
# 示例 1: 综合装饰器 - 一键组合所有优化
# ============================================================

print("\n示例 1: @optimized - 一键组合所有优化")
print("-" * 60)

class FlakyDataService:
    def __init__(self):
        self.call_count = 0
    
    @optimized(
        measure=True,      # 启用性能监控
        cache=True,        # 启用缓存
        retry=True,        # 启用重试
        cache_ttl=30.0,    # 缓存 30 秒
        retry_max=3,       # 最多重试 3 次
        retry_delay=0.3,   # 初始延迟 0.3 秒
        name="data_service"
    )
    def fetch_data(self, data_id: int) -> dict:
        """获取数据的方法（模拟网络请求）"""
        self.call_count += 1
        
        # 模拟前 2 次失败
        if self.call_count < 3:
            logger.info(f"  尝试 {self.call_count} - 模拟失败")
            raise RuntimeError(f"模拟失败 #{self.call_count}")
        
        # 成功时返回数据
        logger.info(f"  尝试 {self.call_count} - 成功获取数据 id={data_id}")
        time.sleep(0.5)  # 模拟耗时操作
        return {"id": data_id, "data": f"数据内容 {data_id}"}

# 测试
service = FlakyDataService()
print("\n第一次调用（会重试）:")
result1 = service.fetch_data(1)
print(f"结果: {result1}")

print("\n第二次调用（缓存命中）:")
result2 = service.fetch_data(1)
print(f"结果: {result2}")

# ============================================================
# 示例 2: 使用预设配置
# ============================================================

print("\n" + "=" * 60)
print("示例 2: 预设优化配置")
print("=" * 60)

print("\n2.1 @optimized_network - 网络请求预设")
print("-" * 60)

class NetworkClient:
    def __init__(self):
        self.request_count = 0
    
    @optimized_network
    def get(self, url: str) -> dict:
        """网络请求"""
        self.request_count += 1
        
        if self.request_count < 2:
            logger.info(f"  请求 {self.request_count} - 连接失败")
            raise ConnectionError("网络连接失败")
        
        logger.info(f"  请求 {self.request_count} - 成功: {url}")
        return {"status": "ok", "url": url}

client = NetworkClient()
result = client.get("https://example.com/api")
print(f"网络请求结果: {result}")

print("\n2.2 @optimized_cacheable - 可缓存操作预设")
print("-" * 60)

@optimized_cacheable(ttl=60.0, prefix="expensive_calc")
def expensive_calculation(x: int, y: int) -> int:
    """耗时计算"""
    logger.info(f"  执行计算: {x} + {y}")
    time.sleep(0.3)
    return x + y

print("第一次计算（执行）:")
result3 = expensive_calculation(10, 20)
print(f"结果: {result3}")

print("第二次计算（缓存命中）:")
result4 = expensive_calculation(10, 20)
print(f"结果: {result4}")

# ============================================================
# 示例 3: 工具函数
# ============================================================

print("\n" + "=" * 60)
print("示例 3: 工具函数")
print("=" * 60)

print("\n获取优化摘要:")
print(get_optimization_summary())

print("\n清空缓存:")
clear_all_caches()
print("缓存已清空")

print("\n重置性能指标:")
reset_all_metrics()
print("指标已重置")

print("\n再次调用计算（缓存失效，重新计算）:")
result5 = expensive_calculation(10, 20)
print(f"结果: {result5}")

# ============================================================
# 示例 4: 发现引擎优化包装示例
# ============================================================

print("\n" + "=" * 60)
print("示例 4: 发现引擎优化包装（概念演示）")
print("=" * 60)

# 假设我们有一个发现引擎
class MockDiscoveryEngine:
    def _build_route_map(self):
        """构建 route_map"""
        logger.info("  构建 route_map（耗时）")
        time.sleep(0.5)
        return {}
    
    def stage_read(self):
        """读取阶段"""
        logger.info("  执行 stage_read")
        time.sleep(0.3)
        return {}
    
    def stage_reason(self):
        """推理阶段"""
        logger.info("  执行 stage_reason")
        time.sleep(1.0)
        return []

# 创建优化后的版本
class OptimizedDiscoveryEngine(MockDiscoveryEngine):
    """优化后的发现引擎"""
    
    @optimized_cacheable(ttl=300.0, prefix="route_map")
    def _build_route_map(self):
        """缓存 route_map"""
        return super()._build_route_map()
    
    @measure_time("stage_read")
    def stage_read(self):
        """监控 stage_read"""
        return super().stage_read()
    
    @measure_time("stage_reason")
    def stage_reason(self):
        """监控 stage_reason"""
        return super().stage_reason()

# 测试优化后的引擎
engine = OptimizedDiscoveryEngine()

print("\n第一次完整流程:")
engine._build_route_map()
engine.stage_read()
engine.stage_reason()

print("\n第二次完整流程（route_map 缓存命中）:")
engine._build_route_map()
engine.stage_read()
engine.stage_reason()

# ============================================================
# 完成
# ============================================================

print("\n" + "=" * 60)
print("最终优化摘要:")
print("=" * 60)
print(get_optimization_summary())

print("\n" + "=" * 60)
print("示例完成！")
print("=" * 60)
print("\n提示：")
print("- 所有优化都是零风险，不修改现有代码")
print("- 可以通过装饰器选择性启用")
print("- 查看 docs/OPTIMIZATION_GUIDE.md 了解更多")

