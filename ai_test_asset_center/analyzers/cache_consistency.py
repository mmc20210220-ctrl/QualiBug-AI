from __future__ import annotations

"""
QualiBug AI - 缓存一致性分析器 (C21)

用于分析缓存、搜索、索引与读写分离问题。
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class CacheIssueType(Enum):
    """缓存问题类型"""
    STALE_READ = "stale_read"  # 脏读
    CACHE_INVALIDATION = "cache_invalidation"  # 缓存失效
    READ_WRITE_SPLIT = "read_write_split"  # 读写分离
    CACHING_MISSING = "caching_missing"  # 缺少缓存


@dataclass
class CacheStrategy:
    """缓存策略"""
    name: str
    endpoint: str
    cache_key: Optional[str] = None
    ttl: Optional[str] = None
    strategy_type: str = "unknown"


@dataclass
class CacheBug:
    """缓存相关bug"""
    bug_id: str
    category: str
    severity: str
    title: str
    description: str
    issue_type: CacheIssueType
    affected_endpoints: List[str]
    evidence: Dict[str, Any]
    reproduction_steps: List[str]
    expected_behavior: str
    actual_behavior: str


class CacheConsistencyAnalyzer:
    """缓存一致性分析器"""

    def __init__(self):
        self.strategies: List[CacheStrategy] = []
        self.bugs: List[CacheBug] = []

        # 缓存关键词
        self.cache_keywords = [
            "cache", "redis", "memcached", "index", "search", "es", "elasticsearch",
            "read_only", "replica", "slave", "master", "读写分离",
            "缓存", "索引", "搜索", "副本", "只读"
        ]

        # 写操作关键词
        self.write_keywords = [
            "update", "modify", "delete", "create", "post", "put", "patch",
            "更新", "修改", "删除", "创建", "写入"
        ]

    def identify_cache_strategies(
        self,
        api_spec: Dict[str, Any],
        prd_text: Optional[str] = None
    ) -> List[CacheStrategy]:
        """
        识别缓存策略

        Args:
            api_spec: API规格
            prd_text: PRD文本

        Returns:
            缓存策略列表
        """
        logger.info("识别缓存策略...")

        strategies = []
        strategy_id = 0

        paths = api_spec.get("paths", {})

        for path, methods in paths.items():
            for method, config in methods.items():
                summary = str(config.get("summary", "")).lower()
                path_lower = path.lower()

                # 检查是否涉及缓存
                if any(kw in summary or kw in path_lower for kw in self.cache_keywords):
                    strategy = CacheStrategy(
                        name=f"Cache_{strategy_id}",
                        endpoint=path,
                        strategy_type="cache"
                    )
                    strategies.append(strategy)
                    strategy_id += 1

                # 检查是否是读操作
                elif method.upper() == "GET":
                    # 可能有缓存
                    strategy = CacheStrategy(
                        name=f"Read_{strategy_id}",
                        endpoint=path,
                        strategy_type="read_operation"
                    )
                    strategies.append(strategy)
                    strategy_id += 1

        self.strategies.extend(strategies)
        logger.info(f"识别到 {len(strategies)} 个缓存策略/操作")
        return strategies

    def check_cache_invalidation(
        self,
        api_spec: Dict[str, Any]
    ) -> List[CacheBug]:
        """
        检查缓存失效

        Args:
            api_spec: API规格

        Returns:
            发现的bug列表
        """
        logger.info("检查缓存失效...")

        bugs = []
        bug_id = 0

        paths = api_spec.get("paths", {})

        # 找出写操作
        write_endpoints = []
        read_endpoints = []

        for path, methods in paths.items():
            for method in methods:
                if method.upper() in ["POST", "PUT", "PATCH", "DELETE"]:
                    write_endpoints.append(path)
                elif method.upper() == "GET":
                    read_endpoints.append(path)

        # 检查每个写操作是否可能有缓存失效问题
        for write_path in write_endpoints:
            for read_path in read_endpoints:
                # 简单检查：路径是否相关
                if self._paths_related(write_path, read_path):
                    bug = CacheBug(
                        bug_id=f"CB_{bug_id:03d}",
                        category="C21",
                        severity="P1",
                        title=f"可能存在缓存失效问题: {write_path}",
                        description=f"写操作 {write_path} 可能导致读操作 {read_path} 的缓存失效问题",
                        issue_type=CacheIssueType.CACHE_INVALIDATION,
                        affected_endpoints=[write_path, read_path],
                        evidence={"write_endpoint": write_path, "read_endpoint": read_path},
                        reproduction_steps=[
                            f"1. 先调用读操作: {read_path}",
                            f"2. 调用写操作: {write_path}",
                            f"3. 再次调用读操作: {read_path}",
                            "4. 验证第二次读取的数据是否是最新的"
                        ],
                        expected_behavior="写操作后，相关的缓存应该被清除或更新",
                        actual_behavior="可能存在缓存未正确失效的问题"
                    )
                    bugs.append(bug)
                    bug_id += 1
                    break  # 每个写操作只检查一次

        self.bugs.extend(bugs)
        logger.info(f"发现 {len(bugs)} 个缓存问题")
        return bugs

    def _paths_related(self, path1: str, path2: str) -> bool:
        """判断两个路径是否相关 — 要求资源级别匹配，排除通用前缀"""
        # 排除通用路径段（api版本号、通用前缀等）
        generic_segments = {
            "api", "v1", "v2", "v3", "app", "service", "internal",
            "rest", "openapi"
        }
        parts1 = set(p for p in path1.split('/') if p and not p.startswith('{') and p.lower() not in generic_segments)
        parts2 = set(p for p in path2.split('/') if p and not p.startswith('{') and p.lower() not in generic_segments)
        # 要求至少一个资源级别段匹配
        return len(parts1 & parts2) >= 1

    def verify_read_write_split(
        self,
        api_spec: Dict[str, Any]
    ) -> List[CacheBug]:
        """
        验证读写分离

        Args:
            api_spec: API规格

        Returns:
            发现的bug列表
        """
        logger.info("验证读写分离...")

        bugs = []
        bug_id = len(self.bugs)

        # 简单检查：查找可能的读写分离
        paths = api_spec.get("paths", {})

        for path in paths:
            if any(kw in path.lower() for kw in ["read", "replica", "slave", "只读", "副本"]):
                bug = CacheBug(
                    bug_id=f"CB_{bug_id:03d}",
                    category="C21",
                    severity="P2",
                    title=f"可能存在读写分离一致性问题: {path}",
                    description=f"该端点可能使用读写分离，需要注意主从延迟问题",
                    issue_type=CacheIssueType.READ_WRITE_SPLIT,
                    affected_endpoints=[path],
                    evidence={"endpoint": path},
                    reproduction_steps=[
                        "1. 执行写操作",
                        "2. 立即执行读操作",
                        "3. 验证是否读到最新数据"
                    ],
                    expected_behavior="应该有机制保证或处理主从延迟",
                    actual_behavior="可能存在读写分离一致性问题"
                )
                bugs.append(bug)
                bug_id += 1

        self.bugs.extend(bugs)
        return bugs

    def analyze_cache_consistency(
        self,
        api_spec: Dict[str, Any],
        prd_text: Optional[str] = None
    ) -> List[CacheBug]:
        """
        综合缓存一致性分析

        Args:
            api_spec: API规格
            prd_text: PRD文本

        Returns:
            发现的bug列表
        """
        strategies = self.identify_cache_strategies(api_spec, prd_text)
        bugs = self.check_cache_invalidation(api_spec)
        bugs.extend(self.verify_read_write_split(api_spec))
        return bugs

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        severity_count = {"P0": 0, "P1": 0, "P2": 0}
        for bug in self.bugs:
            if bug.severity in severity_count:
                severity_count[bug.severity] += 1

        return {
            "total_strategies": len(self.strategies),
            "total_bugs": len(self.bugs),
            "severity_count": severity_count
        }


# 便捷函数
def analyze_cache_consistency(api_spec: Dict[str, Any], prd_text: Optional[str] = None) -> Dict[str, Any]:
    """
    快速分析缓存一致性

    Args:
        api_spec: API规格
        prd_text: PRD文本

    Returns:
        分析结果
    """
    analyzer = CacheConsistencyAnalyzer()
    bugs = analyzer.analyze_cache_consistency(api_spec, prd_text)
    summary = analyzer.get_summary()
    return {
        "strategies": analyzer.strategies,
        "bugs": bugs,
        "summary": summary
    }


def detect_frontend_backend_state_drift(
    api_response: dict[str, Any],
    ui_data: dict[str, Any],
    endpoint: str,
    *,
    tolerance_fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """P3-11: Detect state drift between API response and UI rendering.

    Compares key fields from an API response against the same fields extracted
    from a UI page (e.g., from DOM scraping or HAR recording). Mismatches
    indicate frontend-backend state drift — the API has one value but the UI
    shows another.

    Args:
        api_response: JSON response from API endpoint
        ui_data: Corresponding data extracted from UI rendering
        endpoint: The API endpoint path for evidence
        tolerance_fields: Field names to exclude from comparison (e.g., timestamps)

    Returns:
        List of drift findings
    """
    skip = set(tolerance_fields or [])
    findings: list[dict[str, Any]] = []

    for key in set(api_response) & set(ui_data):
        if key in skip:
            continue
        api_val = api_response.get(key)
        ui_val = ui_data.get(key)
        if api_val != ui_val:
            findings.append({
                "severity": "P2",
                "title": f"前后端状态漂移: {endpoint}.{key}",
                "category": "cache_consistency",
                "risk_type": "frontend_backend_drift",
                "verdict": "confirmed",
                "expected": f"API和UI的{key}字段应一致",
                "actual": f"API={_safe_repr(api_val)}, UI={_safe_repr(ui_val)}",
                "confidence": 0.80,
                "evidence": {
                    "endpoint": endpoint,
                    "field": key,
                    "api_value": str(api_val)[:200],
                    "ui_value": str(ui_val)[:200],
                },
            })

    # Also flag fields present in API but missing from UI
    for key in set(api_response) - set(ui_data):
        if key in skip:
            continue
        findings.append({
            "severity": "P3",
            "title": f"UI缺失API字段: {endpoint}.{key}",
            "category": "cache_consistency",
            "risk_type": "frontend_backend_drift",
            "verdict": "candidate",
            "expected": f"UI应展示API返回的{key}字段",
            "actual": f"API返回了{key}={_safe_repr(api_response[key])}但UI未展示",
            "confidence": 0.60,
            "evidence": {
                "endpoint": endpoint,
                "field": key,
                "api_value": str(api_response[key])[:200],
            },
        })

    return findings


def detect_cache_drift_via_double_get(
    url: str,
    *,
    timeout: float = 8.0,
    interval_seconds: float = 1.0,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """P3-11: Detect cache drift by comparing two consecutive GET responses.

    Issues two GET requests to the same URL with a short interval. If the
    responses differ, it may indicate:
      - Stale cache (first response cached, second fresh)
      - Read-write split inconsistency
      - Cache not invalidated after a recent write

    Returns dict with drift_detected, responses, and findings.
    """
    import urllib.request as _ur
    import urllib.error as _ue
    req_headers = dict(headers or {})
    req_headers.setdefault("User-Agent", "QualiBug-CacheDrift/1.0")
    req_headers.setdefault("Accept", "application/json")

    def _get(u: str) -> tuple[int, str, str]:
        req = _ur.Request(u, headers=req_headers, method="GET")
        try:
            with _ur.urlopen(req, timeout=timeout) as r:
                return int(r.status), r.read(8192).decode("utf-8", errors="replace"), ""
        except _ue.HTTPError as e:
            body = e.read(4096).decode("utf-8", errors="replace") if e.fp else ""
            return int(e.code), body, f"HTTPError:{e.code}"
        except Exception as exc:
            return 0, "", str(exc)[:300]

    status1, body1, err1 = _get(url)
    time.sleep(max(0.1, interval_seconds))
    status2, body2, err2 = _get(url)

    drift_detected = False
    findings: list[dict[str, Any]] = []

    if status1 != status2:
        drift_detected = True
        findings.append({
            "severity": "P1",
            "title": f"缓存漂移(状态码不一致): {url}",
            "category": "cache_consistency",
            "risk_type": "cache_drift",
            "verdict": "confirmed",
            "expected": "连续两次GET应返回一致的状态码",
            "actual": f"首次={status1}, 间隔{interval_seconds}s后={status2}",
            "confidence": 0.90,
            "evidence": {"url": url, "status1": status1, "status2": status2, "interval_s": interval_seconds},
        })
    elif body1 != body2 and status1 >= 200 and status2 >= 200:
        drift_detected = True
        findings.append({
            "severity": "P2",
            "title": f"缓存漂移(响应体不一致): {url}",
            "category": "cache_consistency",
            "risk_type": "cache_drift",
            "verdict": "confirmed",
            "expected": "连续两次GET应返回一致的响应体",
            "actual": f"响应体不同(长度 {len(body1)} vs {len(body2)})",
            "confidence": 0.75,
            "evidence": {
                "url": url,
                "body1_preview": body1[:500],
                "body2_preview": body2[:500],
                "interval_s": interval_seconds,
            },
        })

    return {
        "drift_detected": drift_detected,
        "first_response": {"status": status1, "body_preview": body1[:500], "error": err1},
        "second_response": {"status": status2, "body_preview": body2[:500], "error": err2},
        "interval_seconds": interval_seconds,
        "findings": findings,
    }


def _safe_repr(value: Any) -> str:
    if value is None:
        return "null"
    text = str(value)
    return text[:80] + ("..." if len(text) > 80 else "")
