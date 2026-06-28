from __future__ import annotations

"""
安全的性能监控装饰器 - 不改变核心逻辑，只添加监控
QualiBug AI - 性能监控工具
"""

import time
import logging
from functools import wraps
from typing import Any, Callable, TypeVar, cast

T = TypeVar("T")

# 配置日志
logger = logging.getLogger("qualibug.performance")
logger.setLevel(logging.INFO)

# 避免重复添加 handler
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class PerformanceMetrics:
    """简单的性能指标收集器"""
    
    _instance: PerformanceMetrics | None = None
    _metrics: dict[str, list[float]] = {}
    
    def __new__(cls) -> PerformanceMetrics:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def record(cls, name: str, duration: float) -> None:
        """记录一个方法的执行时间"""
        if name not in cls._metrics:
            cls._metrics[name] = []
        cls._metrics[name].append(duration)
    
    @classmethod
    def get_summary(cls, name: str | None = None) -> dict[str, Any]:
        """获取性能摘要"""
        if name:
            durations = cls._metrics.get(name, [])
            if not durations:
                return {"name": name, "count": 0}
            return {
                "name": name,
                "count": len(durations),
                "min": min(durations),
                "max": max(durations),
                "avg": sum(durations) / len(durations),
                "total": sum(durations)
            }
        
        # 返回所有指标
        summary = {}
        for metric_name in cls._metrics:
            summary[metric_name] = cls.get_summary(metric_name)
        return summary
    
    @classmethod
    def reset(cls, name: str | None = None) -> None:
        """重置指标"""
        if name:
            if name in cls._metrics:
                cls._metrics[name] = []
        else:
            cls._metrics = {}


def measure_time(
    name: str | None = None,
    log_level: int = logging.INFO,
    collect_metrics: bool = True
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    安全的性能监控装饰器 - 不改变被装饰函数的行为
    
    Args:
        name: 指标名称（默认使用函数名）
        log_level: 日志级别
        collect_metrics: 是否收集性能指标
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        metric_name = name or func.__qualname__
        
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.perf_counter() - start_time
                
                # 记录日志
                if log_level is not None:
                    logger.log(
                        log_level,
                        f"[{metric_name}] 执行耗时: {duration:.3f}s"
                    )
                
                # 收集指标
                if collect_metrics:
                    PerformanceMetrics.record(metric_name, duration)
        
        return cast(Callable[..., T], wrapper)
    return decorator


def safe_exception_logger(
    name: str | None = None,
    reraise: bool = True,
    log_level: int = logging.WARNING
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    安全的异常日志装饰器 - 记录异常但不改变行为
    
    Args:
        name: 指标名称
        reraise: 是否重新抛出异常（默认 True，保持原有行为）
        log_level: 日志级别
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        metric_name = name or func.__qualname__
        
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.log(
                    log_level,
                    f"[{metric_name}] 发生异常: {type(e).__name__}: {str(e)}",
                    exc_info=True
                )
                if reraise:
                    raise
                # 如果不重新抛出，返回 None 或合理默认值
                return None  # type: ignore
        
        return cast(Callable[..., T], wrapper)
    return decorator


def get_performance_summary() -> str:
    """获取格式化的性能摘要"""
    summary = PerformanceMetrics.get_summary()
    if not summary:
        return "暂无性能数据"
    
    lines = ["=" * 60, "性能摘要", "=" * 60]
    
    for name, metrics in sorted(summary.items()):
        if metrics["count"] == 0:
            continue
        lines.append(
            f"{name:40s} | "
            f"次数: {metrics['count']:3d} | "
            f"平均: {metrics['avg']:.3f}s | "
            f"最小: {metrics['min']:.3f}s | "
            f"最大: {metrics['max']:.3f}s | "
            f"总计: {metrics['total']:.3f}s"
        )
    
    lines.append("=" * 60)
    return "\n".join(lines)

