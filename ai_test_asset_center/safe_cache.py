from __future__ import annotations

"""
安全的缓存工具 - 零风险，可选使用
QualiBug AI - 缓存优化工具
"""

import time
import hashlib
import json
from typing import Any, Callable, TypeVar, cast
from functools import wraps
from pathlib import Path

T = TypeVar("T")


class SafeCache:
    """
    安全的内存缓存类
    
    特点：
    - 可选使用，不影响现有代码
    - TTL 过期机制
    - 可随时禁用
    """
    
    _instance: SafeCache | None = None
    _cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expiration_time)
    _enabled: bool = True
    _default_ttl: float = 300.0  # 默认 5 分钟
    
    def __new__(cls) -> SafeCache:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def enable(cls) -> None:
        """启用缓存"""
        cls._enabled = True
    
    @classmethod
    def disable(cls) -> None:
        """禁用缓存"""
        cls._enabled = False
    
    @classmethod
    def is_enabled(cls) -> bool:
        """检查缓存是否启用"""
        return cls._enabled
    
    @classmethod
    def set_default_ttl(cls, ttl_seconds: float) -> None:
        """设置默认 TTL"""
        cls._default_ttl = max(1.0, ttl_seconds)
    
    @classmethod
    def _make_key(cls, func_name: str, args: tuple, kwargs: dict) -> str:
        """生成缓存键"""
        key_parts = [func_name]
        for arg in args:
            key_parts.append(str(arg))
        for k, v in sorted(kwargs.items()):
            key_parts.append(f"{k}={v}")
        key_str = "|".join(key_parts)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    @classmethod
    def get(cls, key: str) -> Any | None:
        """获取缓存值"""
        if not cls._enabled:
            return None
        
        if key not in cls._cache:
            return None
        
        value, expiration = cls._cache[key]
        if time.time() > expiration:
            del cls._cache[key]
            return None
        
        return value
    
    @classmethod
    def set(cls, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        """设置缓存值"""
        if not cls._enabled:
            return
        
        ttl = ttl_seconds or cls._default_ttl
        expiration = time.time() + ttl
        cls._cache[key] = (value, expiration)
    
    @classmethod
    def clear(cls) -> None:
        """清空所有缓存"""
        cls._cache.clear()
    
    @classmethod
    def clear_expired(cls) -> None:
        """清理过期的缓存"""
        now = time.time()
        expired_keys = [
            key for key, (_, exp) in cls._cache.items()
            if now > exp
        ]
        for key in expired_keys:
            del cls._cache[key]
    
    @classmethod
    def stats(cls) -> dict[str, Any]:
        """获取缓存统计"""
        cls.clear_expired()
        return {
            "enabled": cls._enabled,
            "count": len(cls._cache),
            "default_ttl": cls._default_ttl
        }


def cached(
    ttl_seconds: float | None = None,
    key_prefix: str = ""
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    安全的缓存装饰器
    
    特点：
    - 可选使用，不影响功能
    - 可随时通过 SafeCache.disable() 禁用
    - 不改变原函数行为
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        func_name = key_prefix or func.__qualname__
        
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            if not SafeCache.is_enabled():
                return func(*args, **kwargs)
            
            cache_key = SafeCache._make_key(func_name, args, kwargs)
            cached_value = SafeCache.get(cache_key)
            
            if cached_value is not None:
                return cached_value
            
            result = func(*args, **kwargs)
            SafeCache.set(cache_key, result, ttl_seconds)
            return result
        
        return cast(Callable[..., T], wrapper)
    return decorator


def safe_ttl_cache(ttl_seconds: float = 300.0):
    """
    兼容的缓存装饰器别名
    
    用法：
        @safe_ttl_cache(ttl_seconds=60.0)
        def my_function():
            ...
    """
    return cached(ttl_seconds=ttl_seconds)


# ============================================================
# 使用示例 - 不修改现有代码的方式
# ============================================================

def create_cached_route_map_builder(original_builder: Callable) -> Callable:
    """
    创建带缓存的 route_map 构建函数
    
    不修改原代码，通过包装实现缓存
    """
    @cached(ttl_seconds=300.0, key_prefix="route_map")
    def cached_builder(*args, **kwargs):
        return original_builder(*args, **kwargs)
    
    return cached_builder


# 便捷函数
def get_cache_stats() -> dict[str, Any]:
    """获取缓存统计信息"""
    return SafeCache.stats()


def clear_cache() -> None:
    """清空缓存"""
    SafeCache.clear()


def enable_cache() -> None:
    """启用缓存"""
    SafeCache.enable()


def disable_cache() -> None:
    """禁用缓存"""
    SafeCache.disable()

