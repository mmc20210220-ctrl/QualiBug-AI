from __future__ import annotations

"""
QualiBug AI - 综合优化工具包
整合所有安全优化，使用更方便

特点：
- 零风险，不修改现有代码
- 一站式导入所有优化
- 预设配置，开箱即用
"""

import logging
from functools import wraps
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# 配置统一的日志
_QUALIBUG_LOGGER = logging.getLogger("qualibug.optimizations")
if not _QUALIBUG_LOGGER.handlers:
    _QUALIBUG_LOGGER.setLevel(logging.INFO)
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    _handler.setFormatter(_formatter)
    _QUALIBUG_LOGGER.addHandler(_handler)

# ============================================================
# 统一导入所有优化模块
# ============================================================

# 性能监控
try:
    from .performance_monitor import (
        measure_time,
        safe_exception_logger,
        PerformanceMetrics,
        get_performance_summary
    )
    _HAS_PERFORMANCE_MONITOR = True
except ImportError:
    _HAS_PERFORMANCE_MONITOR = False
    _QUALIBUG_LOGGER.warning("performance_monitor module not available")

# 安全缓存
try:
    from .safe_cache import (
        cached,
        SafeCache,
        enable_cache,
        disable_cache,
        clear_cache,
        get_cache_stats
    )
    _HAS_SAFE_CACHE = True
except ImportError:
    _HAS_SAFE_CACHE = False
    _QUALIBUG_LOGGER.warning("safe_cache module not available")

# 安全重试
try:
    from .safe_retry import (
        safe_retry,
        safe_retry_network,
        safe_retry_api
    )
    _HAS_SAFE_RETRY = True
except ImportError:
    _HAS_SAFE_RETRY = False
    _QUALIBUG_LOGGER.warning("safe_retry module not available")

# ============================================================
# 综合装饰器 - 一键组合所有优化
# ============================================================

def optimized(
    measure: bool = True,
    cache: bool = False,
    retry: bool = False,
    cache_ttl: float = 300.0,
    cache_key_prefix: str = "",
    retry_max: int = 3,
    retry_delay: float = 0.5,
    name: str | None = None
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    综合优化装饰器 - 一键组合所有优化

    特点：
    - 零风险，不改变原函数行为
    - 可配置是否启用各项优化
    - 自动处理模块缺失的情况

    参数：
        measure: 是否启用性能监控（默认 True）
        cache: 是否启用缓存（默认 False）
        retry: 是否启用重试（默认 False）
        cache_ttl: 缓存 TTL（秒）
        cache_key_prefix: 缓存键前缀
        retry_max: 最大重试次数
        retry_delay: 初始重试延迟（秒）
        name: 操作名称

    使用示例：
        @optimized(measure=True, cache=True, retry=True)
        def my_function():
            # ...

        @optimized(cache=True, cache_ttl=60.0)
        def fetch_data():
            # ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        func_name = name or func.__qualname__
        
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return func(*args, **kwargs)
        
        current_func = wrapper
        
        # 应用性能监控
        if measure and _HAS_PERFORMANCE_MONITOR:
            current_func = measure_time(func_name)(current_func)
        
        # 应用重试
        if retry and _HAS_SAFE_RETRY:
            current_func = safe_retry(
                max_retries=retry_max,
                initial_delay=retry_delay,
                name=func_name
            )(current_func)
        
        # 应用缓存（注意：缓存需要放在最外层）
        if cache and _HAS_SAFE_CACHE:
            current_func = cached(
                ttl_seconds=cache_ttl,
                key_prefix=cache_key_prefix or func_name
            )(current_func)
        
        return current_func
    
    return decorator

# ============================================================
# 预设优化配置 - 开箱即用
# ============================================================

def optimized_network(func: Callable[..., T]) -> Callable[..., T]:
    """
    网络请求优化预设
    - 性能监控
    - 重试（3次）
    - 缓存（可选，默认不缓存）
    """
    if _HAS_PERFORMANCE_MONITOR and _HAS_SAFE_RETRY:
        return measure_time(f"{func.__qualname__}[network]")(
            safe_retry_network(func)
        )
    return func

def optimized_api(func: Callable[..., T]) -> Callable[..., T]:
    """
    API 调用优化预设
    - 性能监控
    - 重试（2次）
    """
    if _HAS_PERFORMANCE_MONITOR and _HAS_SAFE_RETRY:
        return measure_time(f"{func.__qualname__}[api]")(
            safe_retry_api(func)
        )
    return func

def optimized_cacheable(
    ttl: float = 300.0,
    prefix: str = ""
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    可缓存操作优化预设
    - 性能监控
    - 缓存
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if _HAS_PERFORMANCE_MONITOR and _HAS_SAFE_CACHE:
            return measure_time(f"{func.__qualname__}[cached]")(
                cached(ttl_seconds=ttl, key_prefix=prefix or func.__qualname__)(func)
            )
        return func
    return decorator

# ============================================================
# 工具函数
# ============================================================

def enable_all_optimizations() -> None:
    """启用所有优化"""
    if _HAS_SAFE_CACHE:
        enable_cache()
    _QUALIBUG_LOGGER.info("All optimizations enabled")

def disable_all_optimizations() -> None:
    """禁用所有优化"""
    if _HAS_SAFE_CACHE:
        disable_cache()
    _QUALIBUG_LOGGER.info("All optimizations disabled")

def clear_all_caches() -> None:
    """清空所有缓存"""
    if _HAS_SAFE_CACHE:
        clear_cache()
    _QUALIBUG_LOGGER.info("All caches cleared")

def reset_all_metrics() -> None:
    """重置所有性能指标"""
    if _HAS_PERFORMANCE_MONITOR:
        PerformanceMetrics.reset()
    _QUALIBUG_LOGGER.info("All metrics reset")

def get_optimization_summary() -> str:
    """获取优化摘要"""
    lines = ["=" * 60, "Optimization Summary", "=" * 60]
    
    if _HAS_PERFORMANCE_MONITOR:
        lines.append("\nPerformance Metrics:")
        lines.append(get_performance_summary())
    
    if _HAS_SAFE_CACHE:
        lines.append("\nCache Stats:")
        lines.append(str(get_cache_stats()))
    
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)

# ============================================================
# 版本信息
# ============================================================

__version__ = "1.0.0"
__all__ = [
    # 综合装饰器
    "optimized",
    "optimized_network",
    "optimized_api",
    "optimized_cacheable",
    
    # 性能监控
    "measure_time",
    "safe_exception_logger",
    "PerformanceMetrics",
    "get_performance_summary",
    
    # 安全缓存
    "cached",
    "SafeCache",
    "enable_cache",
    "disable_cache",
    "clear_cache",
    "get_cache_stats",
    
    # 安全重试
    "safe_retry",
    "safe_retry_network",
    "safe_retry_api",
    
    # 工具函数
    "enable_all_optimizations",
    "disable_all_optimizations",
    "clear_all_caches",
    "reset_all_metrics",
    "get_optimization_summary"
]

