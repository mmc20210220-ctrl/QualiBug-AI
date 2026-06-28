#!/usr/bin/env python
"""
QualiBug AI - 安全重试装饰器示例（简化版）
展示如何使用 safe_retry 装饰器
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到路径
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import logging

# 导入优化模块
from ai_test_asset_center.safe_retry import (
    safe_retry,
    safe_retry_network,
    safe_retry_api
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

print("=" * 60)
print("QualiBug AI - 安全重试装饰器示例")
print("=" * 60)

# ============================================================
# 示例 1: 基本重试
# ============================================================

print("\n示例 1: 基本重试")
print("-" * 60)

class FlakyService:
    def __init__(self):
        self.fail_count = 0
    
    @safe_retry(max_retries=3, initial_delay=0.5, name="flaky_operation")
    def operation(self):
        self.fail_count += 1
        
        if self.fail_count < 3:
            logger.info(f"  尝试 {self.fail_count} - 模拟失败")
            raise RuntimeError(f"模拟失败 #{self.fail_count}")
        
        logger.info(f"  尝试 {self.fail_count} - 成功！")
        return "成功结果"

service = FlakyService()
try:
    result = service.operation()
    print(f"\n最终结果: {result}")
except Exception as e:
    print(f"\n最终失败: {e}")

# ============================================================
# 示例 2: 网络请求重试
# ============================================================

print("\n示例 2: 网络请求重试")
print("-" * 60)

class NetworkService:
    def __init__(self):
        self.network_fail_count = 0
    
    @safe_retry_network
    def fetch(self):
        self.network_fail_count += 1
        
        if self.network_fail_count < 2:
            logger.info(f"  网络请求 {self.network_fail_count} - 连接失败")
            raise ConnectionError("网络连接失败")
        
        logger.info(f"  网络请求 {self.network_fail_count} - 成功获取数据")
        return {"data": "服务器返回的数据"}

net_service = NetworkService()
try:
    result = net_service.fetch()
    print(f"\n获取到的数据: {result}")
except Exception as e:
    print(f"\n网络请求失败: {e}")

# ============================================================
# 示例 3: API 调用重试
# ============================================================

print("\n示例 3: API 调用重试")
print("-" * 60)

class APIService:
    def __init__(self):
        self.api_fail_count = 0
    
    @safe_retry_api
    def call(self):
        self.api_fail_count += 1
        
        if self.api_fail_count < 2:
            logger.info(f"  API 调用 {self.api_fail_count} - 限流 (429)")
            raise RuntimeError("API 限流，请稍后重试")
        
        logger.info(f"  API 调用 {self.api_fail_count} - 成功")
        return {"status": "ok", "result": "API 响应"}

api_service = APIService()
try:
    result = api_service.call()
    print(f"\nAPI 响应: {result}")
except Exception as e:
    print(f"\nAPI 调用失败: {e}")

print("\n" + "=" * 60)
print("示例完成！")
print("=" * 60)
print("\n查看 docs/OPTIMIZATION_GUIDE.md 了解更多信息")

