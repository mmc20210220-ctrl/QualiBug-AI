from __future__ import annotations

"""
QualiBug AI - 权限与授权分析器 (C03, C04)

用于分析权限、身份认证、组织架构、授权委托问题。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class AuthIssueType(Enum):
    """认证授权问题类型"""
    MISSING_AUTH = "missing_auth"  # 缺少认证
    BROKEN_ACCESS_CONTROL = "broken_access_control"  # 访问控制缺陷
    IDOR = "idor"  # 不安全的直接对象引用
    PRIVILEGE_ESCALATION = "privilege_escalation"  # 权限提升
    INSUFFICIENT_PERMISSION = "insufficient_permission"  # 权限不足


@dataclass
class Permission:
    """权限定义"""
    name: str
    description: str
    required_roles: List[str] = field(default_factory=list)
    endpoints: List[str] = field(default_factory=list)


@dataclass
class AuthBug:
    """认证授权相关bug"""
    bug_id: str
    category: str
    severity: str
    title: str
    description: str
    issue_type: AuthIssueType
    affected_endpoints: List[str]
    evidence: Dict[str, Any]
    reproduction_steps: List[str]
    expected_behavior: str
    actual_behavior: str


class AuthorizationAnalyzer:
    """权限与授权分析器"""

    def __init__(self):
        self.permissions: List[Permission] = []
        self.bugs: List[AuthBug] = []

        # 认证关键词
        self.auth_keywords = [
            "auth", "login", "session", "token", "jwt", "oauth", "permission",
            "role", "admin", "user", "tenant", "organization",
            "认证", "登录", "会话", "令牌", "权限", "角色", "管理员",
            "用户", "租户", "组织"
        ]

        # 敏感操作关键词
        self.sensitive_keywords = [
            "delete", "remove", "update", "modify", "admin", "super", "root",
            "删除", "移除", "更新", "修改", "管理员", "超级", "根"
        ]

    def extract_permission_matrix(
        self,
        prd_text: Optional[str] = None,
        api_spec: Optional[Dict[str, Any]] = None
    ) -> List[Permission]:
        """
        提取权限矩阵

        Args:
            prd_text: PRD文本
            api_spec: API规格

        Returns:
            权限列表
        """
        logger.info("提取权限矩阵...")

        permissions = []
        perm_id = 0

        # 从PRD中提取
        if prd_text:
            lines = prd_text.split('\n')
            for line in lines:
                if any(kw in line.lower() for kw in self.auth_keywords):
                    perm = Permission(
                        name=f"PRD_Permission_{perm_id}",
                        description=line.strip()
                    )
                    permissions.append(perm)
                    perm_id += 1

        # 从API规格中推断
        if api_spec:
            paths = api_spec.get("paths", {})
            for path, methods in paths.items():
                for method, config in methods.items():
                    summary = str(config.get("summary", "")).lower()

                    if any(kw in summary or kw in path.lower() for kw in self.auth_keywords):
                        perm = Permission(
                            name=f"API_Permission_{perm_id}",
                            description=f"权限: {method} {path}",
                            endpoints=[path]
                        )
                        permissions.append(perm)
                        perm_id += 1

        self.permissions.extend(permissions)
        logger.info(f"提取到 {len(permissions)} 个权限定义")
        return permissions

    def check_api_permissions(
        self,
        api_spec: Dict[str, Any]
    ) -> List[AuthBug]:
        """
        检查API权限

        Args:
            api_spec: API规格

        Returns:
            发现的bug列表
        """
        logger.info("检查API权限...")

        bugs = []
        bug_id = 0

        paths = api_spec.get("paths", {})

        for path, methods in paths.items():
            for method, config in methods.items():
                summary = str(config.get("summary", "")).lower()
                path_lower = path.lower()

                # 检查敏感操作
                is_sensitive = any(kw in summary or kw in path_lower for kw in self.sensitive_keywords)

                if is_sensitive:
                    # 检查是否有认证关键词
                    has_auth = any(kw in summary or kw in path_lower for kw in self.auth_keywords)

                    if not has_auth:
                        bug = AuthBug(
                            bug_id=f"AB_{bug_id:03d}",
                            category="C03",
                            severity="P0",
                            title=f"敏感操作可能缺少认证: {method.upper()} {path}",
                            description=f"该端点执行敏感操作，但可能缺少认证或权限检查",
                            issue_type=AuthIssueType.MISSING_AUTH,
                            affected_endpoints=[path],
                            evidence={
                                "path": path,
                                "method": method.upper(),
                                "summary": summary
                            },
                            reproduction_steps=[
                                f"1. 直接请求端点: {method.upper()} {path}",
                                "2. 不提供或提供无效的认证信息",
                                "3. 观察是否仍然能够执行操作"
                            ],
                            expected_behavior="敏感操作应该有适当的认证和权限检查",
                            actual_behavior="可能缺少认证或权限检查"
                        )
                        bugs.append(bug)
                        bug_id += 1

        self.bugs.extend(bugs)
        logger.info(f"发现 {len(bugs)} 个认证授权问题")
        return bugs

    def detect_privilege_escalation(
        self,
        api_spec: Dict[str, Any]
    ) -> List[AuthBug]:
        """
        检测权限提升

        Args:
            api_spec: API规格

        Returns:
            发现的bug列表
        """
        logger.info("检测权限提升...")

        bugs = []
        bug_id = len(self.bugs)

        paths = api_spec.get("paths", {})

        # 检查路径中是否有ID参数
        for path in paths:
            if "{" in path and "}" in path and ("id" in path.lower() or "user" in path.lower()):
                bug = AuthBug(
                    bug_id=f"AB_{bug_id:03d}",
                    category="C03",
                    severity="P0",
                    title=f"可能存在IDOR问题: {path}",
                    description=f"该端点可能存在不安全的直接对象引用问题",
                    issue_type=AuthIssueType.IDOR,
                    affected_endpoints=[path],
                    evidence={"path": path},
                    reproduction_steps=[
                        f"1. 用普通用户账号登录",
                        f"2. 修改URL中的ID参数为其他用户的ID",
                        f"3. 调用端点: {path}",
                        "4. 观察是否能够访问或修改其他用户的数据"
                    ],
                    expected_behavior="应该验证用户是否有权限访问该资源",
                    actual_behavior="可能存在权限提升问题"
                )
                bugs.append(bug)
                bug_id += 1

        self.bugs.extend(bugs)
        return bugs

    def check_multi_tenant_permissions(
        self,
        api_spec: Dict[str, Any]
    ) -> List[AuthBug]:
        """
        检查多租户权限

        Args:
            api_spec: API规格

        Returns:
            发现的bug列表
        """
        logger.info("检查多租户权限...")

        bugs = []
        bug_id = len(self.bugs)

        paths = api_spec.get("paths", {})

        for path in paths:
            if "tenant" in path.lower() or "organization" in path.lower():
                # 检查路径中是否有租户ID
                if "{tenant_id}" in path or "{organization_id}" in path:
                    bug = AuthBug(
                        bug_id=f"AB_{bug_id:03d}",
                        category="C04",
                        severity="P0",
                        title=f"多租户端点需要验证权限: {path}",
                        description=f"该端点需要验证用户是否有权访问该租户的资源",
                        issue_type=AuthIssueType.BROKEN_ACCESS_CONTROL,
                        affected_endpoints=[path],
                        evidence={"path": path},
                        reproduction_steps=[
                            "1. 使用租户A的账号登录",
                            f"2. 修改租户ID为租户B的ID",
                            f"3. 调用端点: {path}",
                            "4. 观察是否能够访问租户B的数据"
                        ],
                        expected_behavior="应该严格验证用户的租户和权限",
                        actual_behavior="可能存在越权访问其他租户数据的风险"
                    )
                    bugs.append(bug)
                    bug_id += 1

        self.bugs.extend(bugs)
        return bugs

    def analyze_authorization(
        self,
        api_spec: Dict[str, Any],
        prd_text: Optional[str] = None
    ) -> List[AuthBug]:
        """
        综合认证授权分析

        Args:
            api_spec: API规格
            prd_text: PRD文本

        Returns:
            发现的bug列表
        """
        permissions = self.extract_permission_matrix(prd_text, api_spec)
        bugs = self.check_api_permissions(api_spec)
        bugs.extend(self.detect_privilege_escalation(api_spec))
        bugs.extend(self.check_multi_tenant_permissions(api_spec))
        return bugs

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        severity_count = {"P0": 0, "P1": 0, "P2": 0}
        for bug in self.bugs:
            if bug.severity in severity_count:
                severity_count[bug.severity] += 1

        return {
            "total_permissions": len(self.permissions),
            "total_bugs": len(self.bugs),
            "severity_count": severity_count
        }


# 便捷函数
def analyze_authorization(api_spec: Dict[str, Any], prd_text: Optional[str] = None) -> Dict[str, Any]:
    """
    快速分析认证授权

    Args:
        api_spec: API规格
        prd_text: PRD文本

    Returns:
        分析结果
    """
    analyzer = AuthorizationAnalyzer()
    bugs = analyzer.analyze_authorization(api_spec, prd_text)
    summary = analyzer.get_summary()
    return {
        "permissions": analyzer.permissions,
        "bugs": bugs,
        "summary": summary
    }
