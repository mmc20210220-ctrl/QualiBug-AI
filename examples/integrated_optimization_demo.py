#!/usr/bin/env python
"""
QualiBug AI - 集成优化演示
展示如何在实际工作流中使用所有优化工具

注意：这是一个演示脚本，不实际调用真实的发现引擎
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import time
import logging

# 导入优化模块
from ai_test_asset_center.performance_monitor import (
    measure_time,
    safe_exception_logger,
    PerformanceMetrics,
    get_performance_summary
)
from ai_test_asset_center.safe_cache import (
    cached,
    SafeCache,
    enable_cache,
    disable_cache,
    get_cache_stats,
    clear_cache
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# 模拟工作流 - 模拟发现引擎的工作流程
# ============================================================

class MockDiscoveryEngine:
    """模拟发现引擎，用于演示优化"""
    
    def __init__(self):
        self.base_url = "http://localhost:8000/api"
    
    @measure_time("mock_build_route_map")
    @cached(ttl_seconds=60.0, key_prefix="demo_route_map")
    def _build_route_map(self):
        """模拟构建 route_map（耗时操作）"""
        logger.info("  [MOCK] 构建 route_map（耗时 1 秒）...")
        time.sleep(1.0)  # 模拟网络请求
        return {
            "GET /api/materials": {"path": "/api/materials"},
            "POST /api/materials": {"path": "/api/materials"},
            "GET /api/orders": {"path": "/api/orders"}
        }
    
    @measure_time("mock_stage_read")
    @safe_exception_logger("mock_stage_read")
    def stage_read(self, prd_text, api_spec):
        """模拟 stage_read"""
        logger.info("  [MOCK] 执行 stage_read（耗时 0.5 秒）...")
        time.sleep(0.5)
        return {"entities": ["material", "order"], "apis": ["GET /api/materials"]}
    
    @measure_time("mock_stage_reason")
    def stage_reason(self, reader_output):
        """模拟 stage_reason_all"""
        logger.info("  [MOCK] 执行 stage_reason（耗时 2 秒）...")
        time.sleep(2.0)
        return [{"hypothesis_id": "h1", "title": "测试假设"}]
    
    @measure_time("mock_stage_execute")
    def stage_execute(self, hypotheses, route_map):
        """模拟 stage_execute"""
        logger.info("  [MOCK] 执行 stage_execute（耗时 1.5 秒）...")
        time.sleep(1.5)
        return [{"hypothesis_id": "h1", "verdict": "confirmed"}]
    
    @measure_time("mock_stage_verify")
    def stage_verify(self, execution_results):
        """模拟 stage_verify"""
        logger.info("  [MOCK] 执行 stage_verify（耗时 0.3 秒）...")
        time.sleep(0.3)
        return [{"hypothesis_id": "h1", "verdict": "confirmed"}]
    
    @measure_time("mock_full_discovery")
    def run_full_discovery(self, prd_text, api_spec):
        """运行完整的发现流程"""
        logger.info("=" * 60)
        logger.info("开始模拟发现流程")
        logger.info("=" * 60)
        
        # 步骤 1: 构建 route_map
        route_map = self._build_route_map()
        
        # 步骤 2: Stage Read
        reader_output = self.stage_read(prd_text, api_spec)
        
        # 步骤 3: Stage Reason
        hypotheses = self.stage_reason(reader_output)
        
        # 步骤 4: Stage Execute
        execution_results = self.stage_execute(hypotheses, route_map)
        
        # 步骤 5: Stage Verify
        final_results = self.stage_verify(execution_results)
        
        logger.info("=" * 60)
        logger.info("发现流程完成")
        logger.info("=" * 60)
        
        return final_results

# ============================================================
# 演示场景
# ============================================================

def demo_1_basic_usage():
    """演示 1: 基本使用 - 缓存 + 监控"""
    print("\n" + "=" * 60)
    print("演示 1: 基本使用 - 缓存 + 性能监控")
    print("=" * 60)
    
    # 启用缓存
    enable_cache()
    
    # 创建引擎
    engine = MockDiscoveryEngine()
    
    # 第一次运行（无缓存）
    print("\n[第一次运行] - 无缓存")
    print("-" * 60)
    engine.run_full_discovery("PRD text", "API spec")
    
    # 第二次运行（有缓存）
    print("\n[第二次运行] - 应该命中缓存")
    print("-" * 60)
    engine.run_full_discovery("PRD text", "API spec")
    
    # 显示统计
    print("\n" + "=" * 60)
    print("性能摘要")
    print("=" * 60)
    print(get_performance_summary())
    
    print("\n" + "=" * 60)
    print("缓存统计")
    print("=" * 60)
    print(get_cache_stats())
    
    # 清理
    clear_cache()
    PerformanceMetrics.reset()

def demo_2_cache_control():
    """演示 2: 缓存控制 - 启用/禁用缓存"""
    print("\n" + "=" * 60)
    print("演示 2: 缓存控制 - 启用/禁用缓存")
    print("=" * 60)
    
    engine = MockDiscoveryEngine()
    
    # 场景 1: 启用缓存
    print("\n[场景 1] 缓存启用")
    print("-" * 60)
    enable_cache()
    engine._build_route_map()  # 第一次，缓存未命中
    engine._build_route_map()  # 第二次，缓存命中
    
    # 场景 2: 禁用缓存
    print("\n[场景 2] 缓存禁用")
    print("-" * 60)
    disable_cache()
    engine._build_route_map()  # 每次都会执行
    engine._build_route_map()  # 每次都会执行
    
    # 显示统计
    print("\n" + "=" * 60)
    print("性能摘要")
    print("=" * 60)
    print(get_performance_summary())
    
    # 清理
    clear_cache()
    PerformanceMetrics.reset()

def demo_3_error_handling():
    """演示 3: 异常日志 - 安全异常处理"""
    print("\n" + "=" * 60)
    print("演示 3: 异常日志 - 安全异常处理")
    print("=" * 60)
    
    @safe_exception_logger("risky_operation")
    def risky_operation(should_fail: bool = False):
        """一个可能失败的操作"""
        if should_fail:
            raise ValueError("模拟操作失败！")
        return "成功！"
    
    # 测试正常情况
    print("\n[正常情况]")
    result = risky_operation(should_fail=False)
    print(f"结果: {result}")
    
    # 测试异常情况
    print("\n[异常情况]")
    try:
        risky_operation(should_fail=True)
    except Exception as e:
        print(f"捕获到异常: {type(e).__name__}: {e}")
    
    # 清理
    PerformanceMetrics.reset()

def demo_4_custom_ttl():
    """演示 4: 自定义 TTL"""
    print("\n" + "=" * 60)
    print("演示 4: 自定义 TTL - 不同缓存时长")
    print("=" * 60)
    
    @cached(ttl_seconds=2.0, key_prefix="short_term")
    def short_term_cache(x: int):
        """短 TTL 缓存"""
        logger.info(f"  执行函数，参数 x={x}")
        time.sleep(0.5)
        return x * 2
    
    @cached(ttl_seconds=30.0, key_prefix="long_term")
    def long_term_cache(x: int):
        """长 TTL 缓存"""
        logger.info(f"  执行函数，参数 x={x}")
        time.sleep(0.5)
        return x * 3
    
    enable_cache()
    
    # 测试短 TTL
    print("\n[短 TTL 缓存（2秒）]")
    print("-" * 60)
    short_term_cache(5)  # 执行
    short_term_cache(5)  # 缓存命中
    print("  等待 3 秒让缓存过期...")
    time.sleep(3.0)
    short_term_cache(5)  # 缓存已过期，重新执行
    
    # 测试长 TTL
    print("\n[长 TTL 缓存（30秒）]")
    print("-" * 60)
    long_term_cache(10)  # 执行
    long_term_cache(10)  # 缓存命中
    
    # 显示统计
    print("\n" + "=" * 60)
    print("缓存统计")
    print("=" * 60)
    print(get_cache_stats())
    
    # 清理
    clear_cache()
    PerformanceMetrics.reset()

def main():
    """主函数 - 运行所有演示"""
    print("=" * 60)
    print("QualiBug AI - 集成优化演示")
    print("=" * 60)
    print("\n这个演示展示如何使用以下优化工具：")
    print("  1. performance_monitor - 性能监控和异常日志")
    print("  2. safe_cache - 安全的内存缓存")
    print("\n所有演示都是安全的，不修改现有代码。")
    
    # 运行演示
    demo_1_basic_usage()
    demo_2_cache_control()
    demo_3_error_handling()
    demo_4_custom_ttl()
    
    # 总结
    print("\n" + "=" * 60)
    print("演示完成！")
    print("=" * 60)
    print("\n下一步：")
    print("  1. 查看 docs/OPTIMIZATION_GUIDE.md 了解详细使用方法")
    print("  2. 创建你自己的 OptimizedDiscoveryEngine 包装类")
    print("  3. 在实际工作流中测试优化效果")

if __name__ == "__main__":
    main()

