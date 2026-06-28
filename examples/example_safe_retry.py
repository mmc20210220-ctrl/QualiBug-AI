#!/usr/bin/env python
"""
QualiBug AI - 安全重试装饰器示例
展示如何使用 safe_retry 装饰器

特点：
- 零风险，不改变现有代码
- 指数退避 + 抖动
- 灵活的配置
"""

from __future__ import annotations

import sys
from pathlib import Path
import random

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import time
import logging

# 导入优化模块
from ai_test_asset_center.safe_retry import (
    safe_retry,
    safe_retry_with_backoff,
    safe_retry_network,
    safe_retry_api
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# 示例 1: 基本重试
# ============================================================

def demo_1_basic_retry():
    """示例 1: 基本重试"""
    print("\n" + "=" * 60)
    print("示例 1: 基本重试 - 简单失败场景")
    print("=" * 60)
    
    fail_count = 0
    
    @safe_retry(max_retries=3, initial_delay=0.5, name="flaky_operation")
    def flaky_operation():
        """一个不稳定的操作"""
        nonlocal fail_count
        fail_count += 1
        
        if fail_count < 3:
            logger.info(f"  尝试 {fail_count} - 模拟失败")
            raise RuntimeError(f"模拟失败 #{fail_count}")
        
        logger.info(f"  尝试 {fail_count} - 成功！")
        return "成功结果"
    
    # 测试
    try:
        result = flaky_operation()
        print(f"\n最终结果: {result}")
    except Exception as e:
        print(f"\n最终失败: {e}")
    
    fail_count = 0  # 重置

# ============================================================
# 示例 2: 网络请求重试
# ============================================================

def demo_2_network_retry():
    """示例 2: 网络请求重试"""
    print("\n" + "=" * 60)
    print("示例 2: 网络请求重试 - 使用预设配置")
    print("=" * 60)
    
    network_fail_count = 0
    
    @safe_retry_network
    def fetch_data_from_server():
        """模拟网络请求"""
        nonlocal network_fail_count
        network_fail_count += 1
        
        # 模拟网络不稳定
        if network_fail_count < 2:
            logger.info(f"  网络请求 {network_fail_count} - 连接失败")
            raise ConnectionError("网络连接失败")
        
        logger.info(f"  网络请求 {network_fail_count} - 成功获取数据")
        return {"data": "服务器返回的数据"}
    
    # 测试
    try:
        result = fetch_data_from_server()
        print(f"\n获取到的数据: {result}")
    except Exception as e:
        print(f"\n网络请求失败: {e}")
    
    network_fail_count = 0  # 重置

# ============================================================
# 示例 3: API 调用重试
# ============================================================

def demo_3_api_retry():
    """示例 3: API 调用重试"""
    print("\n" + "=" * 60)
    print("示例 3: API 调用重试 - 使用预设配置")
    print("=" * 60)
    
    api_fail_count = 0
    
    @safe_retry_api
    def call_external_api():
        """模拟 API 调用"""
        nonlocal api_fail_count
        api_fail_count += 1
        
        # 模拟 API 限流
        if api_fail_count < 2:
            logger.info(f"  API 调用 {api_fail_count} - 限流 (429)")
            raise RuntimeError("API 限流，请稍后重试")
        
        logger.info(f"  API 调用 {api_fail_count} - 成功")
        return {"status": "ok", "result": "API 响应"}
    
    # 测试
    try:
        result = call_external_api()
        print(f"\nAPI 响应: {result}")
    except Exception as e:
        print(f"\nAPI 调用失败: {e}")
    
    api_fail_count = 0  # 重置

# ============================================================
# 示例 4: 指数退避
# ============================================================

def demo_4_exponential_backoff():
    """示例 4: 指数退避演示"""
    print("\n" + "=" * 60)
    print("示例 4: 指数退避 - 延迟递增")
    print("=" * 60)
    
    backoff_fail_count = 0
    
    @safe_retry_with_backoff(
        max_retries=3,
        base_delay=0.3,
        max_delay=1.0
    )
    def operation_with_backoff():
        """演示指数退避"""
        nonlocal backoff_fail_count
        backoff_fail_count += 1
        
        if backoff_fail_count < 4:
            logger.info(f"  尝试 {backoff_fail_count} - 失败")
            raise RuntimeError("模拟失败")
        
        logger.info(f"  尝试 {backoff_fail_count} - 成功")
        return "退避成功"
    
    # 测试
    try:
        start = time.time()
        result = operation_with_backoff()
        elapsed = time.time() - start
        print(f"\n结果: {result}")
        print(f"总耗时: {elapsed:.2f} 秒")
    except Exception as e:
        print(f"\n失败: {e}")
    
    backoff_fail_count = 0  # 重置

# ============================================================
# 示例 5: 多次失败最终成功
# ============================================================

def demo_5_multiple_failures():
    """示例 5: 多次失败最终成功"""
    print("\n" + "=" * 60)
    print("示例 5: 多次失败最终成功 - 真实场景模拟")
    print("=" * 60)
    
    attempt_count = 0
    
    @safe_retry(
        max_retries=5,
        initial_delay=0.2,
        max_delay=1.0,
        backoff_factor=1.5,
        name="unreliable_service"
    )
    def unreliable_service_call():
        """模拟一个非常不稳定的服务"""
        nonlocal attempt_count
        attempt_count += 1
        
        # 模拟随机失败，第 5 次成功
        if attempt_count < 5:
            # 随机选择异常类型
            errors = [
                ConnectionError("连接超时"),
                RuntimeError("服务暂时不可用"),
                TimeoutError("请求超时"),
            ]
            error = random.choice(errors)
            logger.info(f"  尝试 {attempt_count} - {type(error).__name__}: {error}")
            raise error
        
        logger.info(f"  尝试 {attempt_count} - 最终成功！")
        return "服务响应成功"
    
    # 测试
    try:
        result = unreliable_service_call()
        print(f"\n[OK] 最终成功！结果: {result}")
    except Exception as e:
        print(f"\n[FAIL] 最终失败: {e}")
    
    attempt_count = 0  # 重置

# ============================================================
# 示例 6: 与其他优化组合使用
# ============================================================

def demo_6_combined_usage():
    """示例 6: 与性能监控和缓存组合使用"""
    print("\n" + "=" * 60)
    print("示例 6: 组合使用 - 重试 + 监控 + 缓存")
    print("=" * 60)
    
    from ai_test_asset_center.performance_monitor import measure_time
    from ai_test_asset_center.safe_cache import cached, enable_cache
    
    enable_cache()
    
    combined_attempt_count = 0
    
    @measure_time("combined_operation")
    @safe_retry(max_retries=2, initial_delay=0.3, name="combined")
    @cached(ttl_seconds=30.0, key_prefix="combined_cache")
    def combined_operation(x: int):
        """组合使用多个装饰器"""
        nonlocal combined_attempt_count
        combined_attempt_count += 1
        
        if combined_attempt_count < 2:
            logger.info(f"  尝试 {combined_attempt_count} - 失败")
            raise RuntimeError("模拟失败")
        
        logger.info(f"  尝试 {combined_attempt_count} - 成功，参数 x={x}")
        time.sleep(0.5)  # 模拟耗时操作
        return x * 10
    
    # 第一次调用（会重试）
    print("\n[第一次调用]")
    result1 = combined_operation(5)
    print(f"结果: {result1}")
    
    # 第二次调用（缓存命中）
    print("\n[第二次调用]（应该缓存命中）")
    result2 = combined_operation(5)
    print(f"结果: {result2}")
    
    combined_attempt_count = 0  # 重置

# ============================================================
# 主函数
# ============================================================

def main():
    """运行所有示例"""
    print("=" * 60)
    print("QualiBug AI - 安全重试装饰器示例")
    print("=" * 60)
    print("\n这个示例展示如何使用 safe_retry 装饰器：")
    print("  1. 基本重试")
    print("  2. 网络请求重试（预设配置）")
    print("  3. API 调用重试（预设配置）")
    print("  4. 指数退避")
    print("  5. 多次失败最终成功")
    print("  6. 与其他优化组合使用")
    
    # 运行所有示例
    demo_1_basic_retry()
    demo_2_network_retry()
    demo_3_api_retry()
    demo_4_exponential_backoff()
    demo_5_multiple_failures()
    demo_6_combined_usage()
    
    print("\n" + "=" * 60)
    print("所有示例完成！")
    print("=" * 60)
    print("\n下一步：")
    print("  1. 查看 ai_test_asset_center/safe_retry.py 源代码")
    print("  2. 在你的代码中使用装饰器")
    print("  3. 查看 docs/OPTIMIZATION_GUIDE.md 了解更多")

if __name__ == "__main__":
    main()

