from __future__ import annotations

"""
QualiBug AI - 多租户分析器 (C05)

用于分析多租户隔离问题，提升对数据隔离类bug的发现能力。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class IsolationType(Enum):
    """隔离类型"""
    TENANT_ID = "tenant_id"
    ORGANIZATION_ID = "organization_id"
    WORKSPACE_ID = "workspace_id"
    USER_ID = "user_id"


@dataclass
class EndpointInfo:
    """端点信息"""
    path: str
    method: str
    parameters: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    has_isolation_param: bool = False
    isolation_param_names: List[str] = field(default_factory=list)


@dataclass
class CacheInfo:
    """缓存信息"""
    key_pattern: str
    includes_isolation: bool = False
    isolation_param: Optional[str] = None


@dataclass
class TenantIsolationBug:
    """多租户隔离bug"""
    bug_id: str
    category: str
    severity: str
    title: str
    description: str
    affected_endpoints: List[str]
    affected_caches: List[str]
    evidence: Dict[str, Any]
    reproduction_steps: List[str]
    expected_behavior: str
    actual_behavior: str


class MultiTenantAnalyzer:
    """多租户分析器"""

    def __init__(self):
        self.endpoints: List[EndpointInfo] = []
        self.caches: List[CacheInfo] = []
        self.bugs: List[TenantIsolationBug] = []

        # 常见的租户隔离参数名
        self.isolation_keywords = [
            "tenant", "tenant_id", "organization", "organization_id",
            "org_id", "company", "company_id", "workspace", "workspace_id",
            "user", "user_id", "customer", "customer_id", "account", "account_id",
            "租户", "组织", "公司", "用户", "客户", "账户"
        ]

    def analyze_api_endpoints(self, api_spec: Dict[str, Any]) -> List[TenantIsolationBug]:
        """
        分析API端点的租户隔离情况

        Args:
            api_spec: API规格

        Returns:
            发现的bug列表
        """
        logger.info("分析API端点的租户隔离...")

        bugs = []

        # 解析API规格
        paths = api_spec.get("paths", {})

        for path, methods in paths.items():
            for method, config in methods.items():
                endpoint = EndpointInfo(
                    path=path,
                    method=method.upper()
                )

                # 提取参数
                parameters = config.get("parameters", [])
                endpoint.parameters = [p.get("name", "") for p in parameters]

                # 检查是否有租户隔离参数
                self._check_isolation_param(endpoint, parameters)

                self.endpoints.append(endpoint)

                # 检查是否有隔离问题
                endpoint_bugs = self._check_endpoint_isolation(endpoint)
                bugs.extend(endpoint_bugs)

        self.bugs.extend(bugs)
        logger.info(f"分析了 {len(self.endpoints)} 个端点，发现 {len(bugs)} 个潜在问题")

        return bugs

    def _check_isolation_param(self, endpoint: EndpointInfo, parameters: List[Dict[str, Any]]):
        """检查端点是否有隔离参数"""
        for param in parameters:
            param_name = param.get("name", "").lower()
            param_in = param.get("in", "")

            # 检查是否是租户隔离参数
            for keyword in self.isolation_keywords:
                if keyword.lower() in param_name:
                    endpoint.has_isolation_param = True
                    endpoint.isolation_param_names.append(keyword)
                    logger.debug(f"端点 {endpoint.path} 包含隔离参数: {keyword}")
                    break

            if endpoint.has_isolation_param:
                break

        # 如果路径中包含租户占位符
        if "{tenant_id}" in endpoint.path or "{tenant}" in endpoint.path:
            endpoint.has_isolation_param = True
            endpoint.isolation_param_names.append("tenant_id_in_path")

    def _check_endpoint_isolation(self, endpoint: EndpointInfo) -> List[TenantIsolationBug]:
        """检查端点隔离问题"""
        bugs = []
        bug_id = len(self.bugs)

        # 检查1: 读操作是否缺少租户隔离（降为P1，因为多数系统通过JWT隐式隔离）
        if endpoint.method in ["GET", "LIST"]:
            if not endpoint.has_isolation_param and not self._is_public_endpoint(endpoint.path):
                bug = TenantIsolationBug(
                    bug_id=f"MT_{bug_id:03d}",
                    category="C05",
                    severity="P1",
                    title=f"查询端点需验证租户隔离: {endpoint.path}",
                    description=f"读操作 {endpoint.method} {endpoint.path} 路径中未发现租户隔离参数，需验证是否通过认证上下文隐式隔离",
                    affected_endpoints=[endpoint.path],
                    affected_caches=[],
                    evidence={
                        "path": endpoint.path,
                        "method": endpoint.method,
                        "parameters": endpoint.parameters,
                        "note": "租户隔离可能由JWT/Session上下文隐式实现"
                    },
                    reproduction_steps=[
                        f"1. 使用租户A的账号调用 {endpoint.path}",
                        f"2. 尝试修改参数访问租户B的数据",
                        "3. 观察是否成功访问到其他租户数据"
                    ],
                    expected_behavior="查询接口应该确保租户数据隔离",
                    actual_behavior="路径中未发现显式隔离参数，需验证"
                )
                bugs.append(bug)
                bug_id += 1

        # 检查2: 写操作是否有租户隔离（保持P0，写操作风险更高）
        if endpoint.method in ["POST", "PUT", "PATCH", "DELETE"]:
            if not endpoint.has_isolation_param and not self._is_public_endpoint(endpoint.path):
                bug = TenantIsolationBug(
                    bug_id=f"MT_{bug_id:03d}",
                    category="C05",
                    severity="P0",
                    title=f"写操作需验证租户隔离: {endpoint.path}",
                    description=f"写操作 {endpoint.method} {endpoint.path} 路径中未发现租户隔离参数，需验证是否有跨租户写入风险",
                    affected_endpoints=[endpoint.path],
                    affected_caches=[],
                    evidence={
                        "path": endpoint.path,
                        "method": endpoint.method,
                        "parameters": endpoint.parameters
                    },
                    reproduction_steps=[
                        f"1. 使用租户A的账号调用 {endpoint.path} 修改数据",
                        f"2. 尝试修改租户B的数据",
                        "3. 观察是否成功修改其他租户数据"
                    ],
                    expected_behavior="写操作应该确保租户数据隔离",
                    actual_behavior="路径中未发现显式隔离参数"
                )
                bugs.append(bug)
                bug_id += 1

        return bugs

    def _is_public_endpoint(self, path: str) -> bool:
        """判断是否是公开端点"""
        public_keywords = [
            "public", "auth", "login", "register", "health", "metrics",
            "docs", "swagger", "openapi", "config", "static", "favicon",
            "公开", "登录", "注册", "健康", "指标", "文档", "配置"
        ]
        return any(kw in path.lower() for kw in public_keywords)

    def scan_cache_keys(self, cache_configs: List[Dict[str, Any]]) -> List[TenantIsolationBug]:
        """
        扫描缓存key是否包含租户信息

        Args:
            cache_configs: 缓存配置列表

        Returns:
            发现的bug列表
        """
        logger.info("扫描缓存key的租户隔离...")

        bugs = []
        bug_id = len(self.bugs)

        for cache_config in cache_configs:
            key_pattern = cache_config.get("key", "")

            cache_info = CacheInfo(key_pattern=key_pattern)

            # 检查key中是否包含隔离信息
            for keyword in self.isolation_keywords:
                if keyword.lower() in key_pattern.lower():
                    cache_info.includes_isolation = True
                    cache_info.isolation_param = keyword
                    break

            self.caches.append(cache_info)

            # 如果是业务数据缓存但没有隔离信息
            if not cache_info.includes_isolation and self._is_business_data_cache(key_pattern):
                bug = TenantIsolationBug(
                    bug_id=f"MT_{bug_id:03d}",
                    category="C05",
                    severity="P0",
                    title=f"缓存key缺少租户隔离: {key_pattern}",
                    description="业务数据缓存key可能缺少租户隔离信息",
                    affected_endpoints=[],
                    affected_caches=[key_pattern],
                    evidence={"cache_key": key_pattern},
                    reproduction_steps=[
                        f"1. 使用租户A的账号写入数据到缓存: {key_pattern}",
                        f"2. 使用租户B的账号读取相同key的缓存",
                        "3. 观察是否读到租户A的数据"
                    ],
                    expected_behavior="业务数据缓存key应该包含租户隔离信息",
                    actual_behavior="缓存key可能缺少租户隔离信息"
                )
                bugs.append(bug)
                bug_id += 1

        self.bugs.extend(bugs)
        logger.info(f"扫描了 {len(self.caches)} 个缓存配置")

        return bugs

    def _is_business_data_cache(self, key_pattern: str) -> bool:
        """判断是否是业务数据缓存"""
        business_keywords = [
            "order", "user", "customer", "product", "inventory", "payment",
            "订单", "用户", "客户", "商品", "库存", "支付"
        ]
        return any(kw in key_pattern.lower() for kw in business_keywords)

    def verify_export_functions(self, api_spec: Dict[str, Any]) -> List[TenantIsolationBug]:
        """
        验证导出功能的租户隔离

        Args:
            api_spec: API规格

        Returns:
            发现的bug列表
        """
        logger.info("验证导出功能的租户隔离...")

        bugs = []
        bug_id = len(self.bugs)

        paths = api_spec.get("paths", {})

        for path in paths:
            # 查找导出相关的端点
            if any(kw in path.lower() for kw in [
                "export", "download", "report", "导出", "下载", "报表"
            ]):
                # 检查是否有隔离参数
                has_isolation = False
                for keyword in self.isolation_keywords:
                    if keyword.lower() in path.lower():
                        has_isolation = True
                        break

                if not has_isolation:
                    bug = TenantIsolationBug(
                        bug_id=f"MT_{bug_id:03d}",
                        category="C05",
                        severity="P0",
                        title=f"导出功能缺少租户隔离: {path}",
                        description="导出功能可能缺少租户隔离，导致导出其他租户数据",
                        affected_endpoints=[path],
                        affected_caches=[],
                        evidence={"export_endpoint": path},
                        reproduction_steps=[
                            f"1. 使用租户A的账号调用导出接口: {path}",
                            f"2. 尝试修改参数导出租户B的数据",
                            "3. 观察导出结果是否包含其他租户数据"
                        ],
                        expected_behavior="导出功能应该包含租户隔离参数，防止数据泄露",
                        actual_behavior="可能缺少租户隔离参数"
                    )
                    bugs.append(bug)
                    bug_id += 1

        self.bugs.extend(bugs)
        return bugs

    def check_tenant_isolation(
        self,
        api_spec: Dict[str, Any],
        cache_configs: Optional[List[Dict[str, Any]]] = None
    ) -> List[TenantIsolationBug]:
        """
        综合检查多租户隔离

        Args:
            api_spec: API规格
            cache_configs: 缓存配置（可选）

        Returns:
            所有发现的bug
        """
        bugs = []

        # 检查API端点
        bugs.extend(self.analyze_api_endpoints(api_spec))

        # 检查缓存
        if cache_configs:
            bugs.extend(self.scan_cache_keys(cache_configs))

        # 检查导出功能
        bugs.extend(self.verify_export_functions(api_spec))

        return bugs

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        return {
            "total_endpoints": len(self.endpoints),
            "endpoints_with_isolation": sum(1 for e in self.endpoints if e.has_isolation_param),
            "total_caches": len(self.caches),
            "caches_with_isolation": sum(1 for c in self.caches if c.includes_isolation),
            "total_bugs": len(self.bugs),
            "bugs_by_severity": {
                "P0": sum(1 for b in self.bugs if b.severity == "P0"),
                "P1": sum(1 for b in self.bugs if b.severity == "P1"),
                "P2": sum(1 for b in self.bugs if b.severity == "P2")
            }
        }


# 便捷函数
def analyze_multi_tenant_isolation(api_spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    快速分析多租户隔离

    Args:
        api_spec: API规格

    Returns:
        分析结果
    """
    analyzer = MultiTenantAnalyzer()
    bugs = analyzer.check_tenant_isolation(api_spec)
    summary = analyzer.get_summary()
    return {
        "bugs": bugs,
        "summary": summary
    }
