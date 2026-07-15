from __future__ import annotations

"""
安全的重试装饰器 - 零风险，可选使用

特点：
- 不改变原函数行为，只是在失败时重试
- 可配置重试次数、延迟、重试异常类型
- 可选使用，可随时移除
- 零风险，不修改现有代码
"""

import time
import random
import logging
from functools import wraps
from typing import Any, Callable, TypeVar, Tuple, cast

T = TypeVar("T")

# 配置日志
logger = logging.getLogger("qualibug.safe_retry")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class SafeRetryConfig:
    """重试配置"""
    
    DEFAULT_MAX_RETRIES: int = 3
    DEFAULT_INITIAL_DELAY: float = 1.0  # 秒
    DEFAULT_MAX_DELAY: float = 30.0  # 秒
    DEFAULT_BACKOFF_FACTOR: float = 2.0
    DEFAULT_JITTER: float = 0.1  # 添加随机抖动，避免雪崩
    DEFAULT_RETRY_EXCEPTIONS: Tuple[type, ...] = (
        Exception,  # 默认重试所有异常
    )


def safe_retry(
    max_retries: int = SafeRetryConfig.DEFAULT_MAX_RETRIES,
    initial_delay: float = SafeRetryConfig.DEFAULT_INITIAL_DELAY,
    max_delay: float = SafeRetryConfig.DEFAULT_MAX_DELAY,
    backoff_factor: float = SafeRetryConfig.DEFAULT_BACKOFF_FACTOR,
    jitter: float = SafeRetryConfig.DEFAULT_JITTER,
    retry_exceptions: Tuple[type, ...] = SafeRetryConfig.DEFAULT_RETRY_EXCEPTIONS,
    name: str | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    安全的重试装饰器
    
    特点：
    - 零风险：不改变原函数行为，只是在失败时重试
    - 指数退避：每次重试延迟递增
    - 抖动：添加随机延迟避免重试风暴
    - 可配置：灵活的重试策略
    
    Args:
        max_retries: 最大重试次数（不包括第一次尝试）
        initial_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        backoff_factor: 退避因子（每次重试延迟乘以该值）
        jitter: 抖动因子（添加随机延迟的比例）
        retry_exceptions: 需要重试的异常类型元组
        name: 操作名称（用于日志）
    
    Returns:
        装饰器函数
    """
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        func_name = name or func.__qualname__
        
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Exception | None = None
            delay = initial_delay
            
            for attempt in range(max_retries + 1):  # +1 是因为第一次尝试不算重试
                try:
                    if attempt > 0:
                        logger.info(
                            f"[{func_name}] 重试 {attempt}/{max_retries}..."
                        )
                    
                    result = func(*args, **kwargs)
                    
                    if attempt > 0:
                        logger.info(
                            f"[{func_name}] 重试成功！"
                        )
                    
                    return result
                    
                except retry_exceptions as e:
                    last_exception = e
                    
                    if attempt >= max_retries:
                        logger.warning(
                            f"[{func_name}] 已达到最大重试次数 ({max_retries})，放弃重试"
                        )
                        raise
                    
                    # 计算延迟
                    jitter_amount = random.uniform(-jitter, jitter) * delay
                    actual_delay = min(max_delay, delay + jitter_amount)
                    actual_delay = max(0, actual_delay)  # 确保延迟不为负
                    
                    logger.warning(
                        f"[{func_name}] 尝试 {attempt + 1}/{max_retries + 1} 失败: "
                        f"{type(e).__name__}: {e}"
                    )
                    logger.info(
                        f"[{func_name}] 等待 {actual_delay:.2f} 秒后重试..."
                    )
                    
                    time.sleep(actual_delay)
                    
                    # 指数退避
                    delay = min(max_delay, delay * backoff_factor)
            
            # 理论上不会到达这里，因为循环内会 raise
            if last_exception:
                raise last_exception
            raise RuntimeError("Unexpected retry exit")
        
        return cast(Callable[..., T], wrapper)
    
    return decorator


def safe_retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    简化的安全重试装饰器 - 使用指数退避
    
    这是 safe_retry 的简化版本，配置更简单
    
    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
    
    Returns:
        装饰器函数
    """
    return safe_retry(
        max_retries=max_retries,
        initial_delay=base_delay,
        max_delay=max_delay,
        backoff_factor=2.0,
        jitter=0.1,
    )


# ============================================================
# 快捷装饰器 - 预设配置
# ============================================================

def safe_retry_network(func: Callable[..., T]) -> Callable[..., T]:
    """
    网络请求专用重试装饰器
    
    预设配置：
    - 最多重试 3 次
    - 初始延迟 1 秒
    - 最大延迟 10 秒
    """
    return safe_retry(
        max_retries=3,
        initial_delay=1.0,
        max_delay=10.0,
        backoff_factor=2.0,
        jitter=0.1,
        name=f"{func.__qualname__}[network]",
    )(func)


def safe_retry_api(func: Callable[..., T]) -> Callable[..., T]:
    """
    API 调用专用重试装饰器
    
    预设配置：
    - 最多重试 2 次
    - 初始延迟 0.5 秒
    - 最大延迟 5 秒
    """
    return safe_retry(
        max_retries=2,
        initial_delay=0.5,
        max_delay=5.0,
        backoff_factor=1.5,
        jitter=0.1,
        name=f"{func.__qualname__}[api]",
    )(func)

