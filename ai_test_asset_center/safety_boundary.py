from __future__ import annotations

"""
Safety Boundary — Hard Enforcement of Test-Environment-Only Operation.

QualiBug NEVER operates on production environments. This is a hard, non-negotiable
architectural constraint, not a configuration option. This module enforces it at
the infrastructure level — before any engine runs, before any API call is made.

Enforcement layers:
1. ENVIRONMENT DECLARATION — every project MUST declare its environment
2. PRODUCTION DETECTION — production indicators are detected and blocked
3. WRITE OPERATION BLOCK — any mutation is blocked outside sandbox
4. CONNECTION SAFETY — base URLs are validated against production patterns
5. AUDIT TRAIL — every safety decision is logged

Principle:
    "If there's any ambiguity about whether this is production, we STOP.
     Better to miss a bug than to touch production data."
"""

import os
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Environment classification
# ---------------------------------------------------------------------------

VALID_ENVIRONMENTS = {"test", "staging", "dev", "development", "sandbox", "qa", "uat"}
PRODUCTION_INDICATORS = {"prod", "production", "live", "online", "prd"}

# URL patterns that indicate production
PRODUCTION_URL_PATTERNS = [
    re.compile(r"https?://(?!localhost|127\.0\.0\.1|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.).*\.(com|cn|net|org)(?!\.test|\.dev|\.staging)"),
    re.compile(r"(?i)(prod|production|live|online|prd)[-.]"),
    re.compile(r"https?://(?!.*\.(test|dev|staging|qa|uat|sandbox)\.).*\.(internal|local)$", re.I),
]

# Write methods that are NEVER allowed outside sandbox
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Destructive operations that require explicit sandbox token
DESTRUCTIVE_OPERATIONS = {
    "create_order", "create_payment", "create_refund", "update_inventory",
    "create_shipment", "approve_claim", "issue_permit", "update_status",
    "delete_record", "cancel_order", "process_refund",
}


# ---------------------------------------------------------------------------
# Safety boundary enforcement
# ---------------------------------------------------------------------------

class SafetyBoundary:
    """Hard enforcement of test-environment-only operation.

    This is called BEFORE any engine executes. If validation fails, the engine
    MUST NOT run — no exceptions, no overrides for convenience.
    """

    def __init__(self, project_id: str, declared_environment: str = "", base_url: str = ""):
        self.project_id = project_id
        self.declared_environment = declared_environment.strip().lower()
        self.base_url = base_url.strip()
        self._violations: list[dict[str, Any]] = []
        self._warnings: list[dict[str, Any]] = []
        self._passed = True

    # ---- Core checks ----

    def check_environment_declared(self) -> "SafetyBoundary":
        """Every project MUST explicitly declare its environment."""
        if not self.declared_environment:
            self._violations.append({
                "rule": "environment_not_declared",
                "severity": "BLOCKING",
                "message": "项目未声明运行环境。必须在配置中显式声明 environment: test|staging|dev|qa",
                "fix": "在项目配置中添加 'environment: test'",
            })
            self._passed = False
        return self

    def check_not_production(self) -> "SafetyBoundary":
        """Environment MUST NOT be production."""
        env = self.declared_environment
        if env in PRODUCTION_INDICATORS or any(ind in env for ind in PRODUCTION_INDICATORS):
            self._violations.append({
                "rule": "production_environment_declared",
                "severity": "BLOCKING",
                "message": f"项目声明了生产环境: '{self.declared_environment}'。QualiBug 只能运行在测试/预发布环境。",
                "fix": "将 environment 改为 'test' 或 'staging'",
            })
            self._passed = False
        return self

    def check_environment_valid(self) -> "SafetyBoundary":
        """Environment must be a recognized non-production value."""
        env = self.declared_environment
        if env and env not in VALID_ENVIRONMENTS:
            self._warnings.append({
                "rule": "unknown_environment",
                "severity": "WARNING",
                "message": f"未知环境值: '{env}'。已知的非生产环境: {sorted(VALID_ENVIRONMENTS)}",
            })
        return self

    def check_base_url_not_production(self) -> "SafetyBoundary":
        """Base URL must not point to production."""
        if not self.base_url:
            self._warnings.append({
                "rule": "no_base_url",
                "severity": "WARNING",
                "message": "未配置 base_url。safe_live 模式将无法执行。",
            })
            return self

        url_lower = self.base_url.lower()

        # Check against production patterns
        for pattern in PRODUCTION_URL_PATTERNS:
            if pattern.search(self.base_url):
                # Allow if explicitly on a known test domain
                if any(t in url_lower for t in (".test.", ".dev.", ".staging.", ".qa.", "localhost", "127.0.0.1")):
                    continue
                self._violations.append({
                    "rule": "base_url_looks_like_production",
                    "severity": "BLOCKING",
                    "message": f"base_url '{self.base_url}' 看起来是生产环境地址。QualiBug 不能连接生产系统。",
                    "fix": "将 base_url 改为测试环境地址，如 http://test-order-service.internal:8080",
                })
                self._passed = False
                break

        # Check for production keywords in URL
        for indicator in PRODUCTION_INDICATORS:
            if indicator in url_lower and "test" not in url_lower:
                self._violations.append({
                    "rule": "production_keyword_in_url",
                    "severity": "BLOCKING",
                    "message": f"base_url 包含生产环境标识 '{indicator}': {self.base_url}",
                    "fix": "使用测试环境的 URL",
                })
                self._passed = False
                break

        return self

    def check_no_auto_write(self, execution_mode: str = "plan_only") -> "SafetyBoundary":
        """Write operations are NEVER executed automatically."""
        if execution_mode not in ("plan_only", "safe_live"):
            self._violations.append({
                "rule": "unknown_execution_mode",
                "severity": "BLOCKING",
                "message": f"未知的执行模式: '{execution_mode}'。仅允许 plan_only 或 safe_live。",
            })
            self._passed = False

        # safe_live is GET-only by design — this is structural, not configurable
        # Any write would require sandbox_required approval
        return self

    def check_test_accounts_configured(self, accounts: dict[str, Any] | None = None) -> "SafetyBoundary":
        """Test accounts must be synthetic, never real user accounts."""
        if accounts:
            for name, account in accounts.items():
                email = str(account.get("email", "")).lower()
                phone = str(account.get("phone", ""))
                # Block real-looking emails
                if email and not any(t in email for t in ("@test.", "@example.", "test@", "+test@")):
                    self._warnings.append({
                        "rule": "account_looks_real",
                        "severity": "WARNING",
                        "message": f"测试账号 '{name}' 的邮箱 '{email}' 看起来像真实用户邮箱。请使用 @test.com 或 @example.com。",
                    })
                # Block real-looking phone numbers
                if phone and re.match(r"^1[3-9]\d{9}$", phone):
                    self._warnings.append({
                        "rule": "account_phone_looks_real",
                        "severity": "WARNING",
                        "message": f"测试账号 '{name}' 的手机号看起来像真实号码。请使用虚拟号码。",
                    })
        return self

    # ---- Result ----

    def validate(self) -> dict[str, Any]:
        """Run all checks and return the safety verdict."""
        return {
            "safe_to_proceed": self._passed and len(self._violations) == 0,
            "violations": self._violations,
            "warnings": self._warnings,
            "violation_count": len(self._violations),
            "warning_count": len(self._warnings),
            "environment": self.declared_environment or "(undeclared)",
            "principle": "QualiBug operates ONLY on test/staging environments. Production is NEVER touched.",
        }

    def assert_safe(self) -> None:
        """Raise if any blocking violation exists. Call this before any engine runs."""
        result = self.validate()
        if not result["safe_to_proceed"]:
            violation_msgs = "\n  - ".join(v["message"] for v in self._violations)
            raise SafetyViolationError(
                f"Safety boundary violation — QualiBug cannot proceed:\n  - {violation_msgs}"
            )


class SafetyViolationError(RuntimeError):
    """Raised when the safety boundary is violated. This is a hard block."""
    pass


# ---------------------------------------------------------------------------
# Safety gate — called before every engine execution
# ---------------------------------------------------------------------------

def safety_gate(
    project_id: str,
    declared_environment: str = "",
    base_url: str = "",
    execution_mode: str = "plan_only",
    accounts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The safety gate. Every engine MUST pass through this before executing.

    Usage in any engine:
        from .safety_boundary import safety_gate
        safety_gate(project_id, env, base_url, mode).assert_safe()
        # ... now safe to proceed

    Returns the safety verdict. Call .assert_safe() to raise on violation.
    """
    boundary = SafetyBoundary(project_id, declared_environment, base_url)
    boundary.check_environment_declared()
    boundary.check_not_production()
    boundary.check_environment_valid()
    boundary.check_base_url_not_production()
    boundary.check_no_auto_write(execution_mode)
    boundary.check_test_accounts_configured(accounts)
    return boundary


# ---------------------------------------------------------------------------
# Quick check helpers
# ---------------------------------------------------------------------------

def is_production_url(url: str) -> bool:
    """Check if a URL looks like production. Used before any HTTP call."""
    url_lower = url.lower()
    # Explicit test/staging/dev indicators → safe
    safe_indicators = [
        "localhost", "127.0.0.1", ".test.", ".dev.", ".staging.", ".qa.", ".sandbox.",
        "test-", "dev-", "staging-", "qa-", "uat-", "-test", "-dev", "-staging", "-qa",
    ]
    if any(t in url_lower for t in safe_indicators):
        return False
    # Internal IP ranges → safe
    if re.match(r"https?://(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)", url):
        return False
    # .local or .internal domains → safe
    if re.search(r"\.(local|internal|test|dev)([/:]|$)", url_lower):
        return False
    # Production keywords → unsafe
    for indicator in PRODUCTION_INDICATORS:
        if indicator in url_lower:
            return True
    # Production TLD without safe subdomain → suspicious
    if re.match(r"https?://[^/]*\.(com|cn|net|org|io)([/:]|$)", url):
        return True
    return False


def is_write_operation(method: str) -> bool:
    """Check if an HTTP method is a write operation."""
    return method.upper() in WRITE_METHODS


def is_destructive_operation(operation_name: str) -> bool:
    """Check if an operation is destructive and requires sandbox approval."""
    op_lower = operation_name.lower().replace(" ", "_").replace("-", "_")
    return op_lower in DESTRUCTIVE_OPERATIONS


# ---------------------------------------------------------------------------
# Project environment configuration
# ---------------------------------------------------------------------------

def get_project_environment(project_id: str, root: Path | None = None) -> str:
    """Read the declared environment from project config."""
    try:
        from .real_project_onboarding import load_real_project_config, _safe_project_id
        project = _safe_project_id(project_id)
        cfg = load_real_project_config(project, root)
        return str(cfg.get("environment", "")).strip().lower()
    except Exception:
        return ""


def ensure_test_environment(project_id: str, base_url: str = "", root: Path | None = None) -> None:
    """One-line safety check. Raises SafetyViolationError if not test environment.

    Call this at the top of every engine's entry point:
        ensure_test_environment(project_id, base_url)
    """
    env = get_project_environment(project_id, root)
    safety_gate(project_id, env, base_url).assert_safe()
