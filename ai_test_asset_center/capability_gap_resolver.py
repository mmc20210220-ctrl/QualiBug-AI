"""Capability Gap Resolver — Unified plan_only detection, classification, and resolution.

This module sits on top of the Phase93A-G pipeline and provides:
  1. Root cause identification for every plan_only hypothesis
  2. Gap classification: auto-resolvable, needs config, needs approval, permanently blocked
  3. Clear, actionable configuration tasks for each gap
  4. Auto-resolution when conditions are met (e.g., base_url becomes available)
  5. Gap summary for dashboard / scan output

Design principles (per AGENTS.md):
  - Reuses existing Phase93 preflight/checks — does not replace them
  - Industry-agnostic: gap detection based on generic preflight checks
  - No fake data: only marks resolved when preflight ACTUALLY passes
  - Observable: every gap has root_cause, impact, resolution_steps
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ═════════════════════════════════════════════════════════════════════════════
# Enums
# ═════════════════════════════════════════════════════════════════════════════

class GapRootCause(str, Enum):
    """Why a capability or probe is stuck in plan_only mode."""
    MISSING_BASE_URL = "missing_base_url"
    MISSING_AUTH = "missing_auth"
    MISSING_OPENAPI = "missing_openapi"
    MISSING_TEST_ACCOUNTS = "missing_test_accounts"
    PRODUCTION_TARGET = "production_target"
    SAFETY_BOUNDARY = "safety_boundary"
    BROWSER_DISABLED = "browser_disabled"
    TESTOPS_OFFLINE = "testops_offline"
    WRITE_SANDBOX_MISSING = "write_sandbox_missing"
    CLEANUP_MISSING = "cleanup_missing"
    SNAPSHOT_MISSING = "snapshot_missing"
    CONFIG_PLACEHOLDERS = "config_placeholders"
    DOCUMENT_GROUNDING = "document_grounding"
    INTERACTIVE_AUTH_BLOCKER = "interactive_auth_blocker"
    NO_CREDENTIALS = "no_credentials"
    NO_SOURCE = "no_source"
    NO_API_SPEC = "no_api_spec"
    UNKNOWN = "unknown"


class GapResolution(str, Enum):
    """How a gap can be resolved."""
    AUTO_RESOLVABLE = "auto_resolvable"          # System can fix automatically
    NEEDS_CUSTOMER_CONFIG = "needs_customer_config"  # Customer must provide config values
    NEEDS_APPROVAL = "needs_approval"             # Needs quality/sandbox approval
    NEEDS_INFRA = "needs_infra"                   # Needs infra change (browser, TestOps)
    PERMANENTLY_BLOCKED = "permanently_blocked"   # Cannot be resolved (e.g., production)


# ═════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ConfigTask:
    """A concrete configuration task to resolve a gap."""
    task_id: str
    priority: str  # P0, P1, P2
    title: str
    description: str
    config_keys: list[str]
    config_example: dict[str, Any]
    validation_steps: list[str]
    estimated_effort: str  # "minutes", "hours", "days"


@dataclass
class CapabilityGap:
    """One detected capability gap with root cause and resolution path."""
    gap_id: str
    root_cause: GapRootCause
    resolution: GapResolution
    priority: str  # P0, P1, P2
    summary: str
    affected_defect_families: list[str] = field(default_factory=list)
    affected_probe_count: int = 0
    affected_capability_ids: list[str] = field(default_factory=list)
    preflight_check_names: list[str] = field(default_factory=list)
    config_task: ConfigTask | None = None
    detected_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolutionResult:
    """Result of attempting to auto-resolve a gap."""
    gap_id: str
    resolved: bool
    reason: str
    promoted_probe_count: int = 0
    promoted_family_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)


# ═════════════════════════════════════════════════════════════════════════════
# Root Cause → Resolution Mapping
# ═════════════════════════════════════════════════════════════════════════════

ROOT_CAUSE_RESOLUTION: dict[GapRootCause, GapResolution] = {
    GapRootCause.MISSING_BASE_URL: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.MISSING_AUTH: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.MISSING_OPENAPI: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.MISSING_TEST_ACCOUNTS: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.PRODUCTION_TARGET: GapResolution.PERMANENTLY_BLOCKED,
    GapRootCause.SAFETY_BOUNDARY: GapResolution.NEEDS_APPROVAL,
    GapRootCause.BROWSER_DISABLED: GapResolution.NEEDS_INFRA,
    GapRootCause.TESTOPS_OFFLINE: GapResolution.NEEDS_INFRA,
    GapRootCause.WRITE_SANDBOX_MISSING: GapResolution.NEEDS_APPROVAL,
    GapRootCause.CLEANUP_MISSING: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.SNAPSHOT_MISSING: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.CONFIG_PLACEHOLDERS: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.DOCUMENT_GROUNDING: GapResolution.AUTO_RESOLVABLE,
    GapRootCause.INTERACTIVE_AUTH_BLOCKER: GapResolution.NEEDS_INFRA,
    GapRootCause.NO_CREDENTIALS: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.NO_SOURCE: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.NO_API_SPEC: GapResolution.NEEDS_CUSTOMER_CONFIG,
    GapRootCause.UNKNOWN: GapResolution.NEEDS_CUSTOMER_CONFIG,
}


# ═════════════════════════════════════════════════════════════════════════════
# Preflight Check → Root Cause Mapping
# ═════════════════════════════════════════════════════════════════════════════

CHECK_TO_ROOT_CAUSE: dict[str, GapRootCause] = {
    "base_url_configured": GapRootCause.MISSING_BASE_URL,
    "url_parse_ok": GapRootCause.MISSING_BASE_URL,
    "url_host_resolves": GapRootCause.MISSING_BASE_URL,
    "base_url_reachable": GapRootCause.MISSING_BASE_URL,
    "non_production_target": GapRootCause.PRODUCTION_TARGET,
    "probe_plan_grounded": GapRootCause.DOCUMENT_GROUNDING,
    "auth_session_ready": GapRootCause.MISSING_AUTH,
    "service_credentials_verified": GapRootCause.MISSING_AUTH,
    "interactive_auth_not_blocked": GapRootCause.INTERACTIVE_AUTH_BLOCKER,
    "token_cookie_or_session_acquired": GapRootCause.MISSING_AUTH,
    "session_health_verified": GapRootCause.MISSING_AUTH,
    "authenticated_api_smoke_verified": GapRootCause.MISSING_AUTH,
    "auth_session_refresh_ready": GapRootCause.MISSING_AUTH,
    "role_coverage": GapRootCause.MISSING_TEST_ACCOUNTS,
    "auto_fixture_create_permission": GapRootCause.WRITE_SANDBOX_MISSING,
    "cleanup_health_declared": GapRootCause.CLEANUP_MISSING,
    "snapshot_observer_ready": GapRootCause.SNAPSHOT_MISSING,
    "config_placeholders_resolved": GapRootCause.CONFIG_PLACEHOLDERS,
}

# Scan preflight reason codes → root cause
SCAN_REASON_TO_ROOT_CAUSE: dict[str, GapRootCause] = {
    "NO_CREDENTIALS": GapRootCause.NO_CREDENTIALS,
    "NO_SOURCE": GapRootCause.NO_SOURCE,
    "NO_API_SPEC": GapRootCause.NO_API_SPEC,
    "NO_TARGET": GapRootCause.MISSING_BASE_URL,
}


# ═════════════════════════════════════════════════════════════════════════════
# Config Task Templates
# ═════════════════════════════════════════════════════════════════════════════

CONFIG_TASK_TEMPLATES: dict[GapRootCause, dict[str, Any]] = {
    GapRootCause.MISSING_BASE_URL: {
        "title": "Configure staging/QA base URL",
        "description": "Runtime evidence cannot be collected without a reachable test target. Provide a non-production staging URL.",
        "config_keys": ["base_url", "environment_kind"],
        "config_example": {"base_url": "https://staging.example.com", "environment_kind": "staging"},
        "validation_steps": ["Set base_url in Settings > Service", "Run preflight check", "Confirm base_url_reachable passes"],
        "estimated_effort": "minutes",
    },
    GapRootCause.MISSING_AUTH: {
        "title": "Provide authentication configuration",
        "description": "Auth, privacy, and ownership probes need real staging sessions with test accounts.",
        "config_keys": ["auth_flow", "accounts", "default_account"],
        "config_example": {
            "auth_flow": {"login_path": "/api/login", "method": "POST", "token_json_path": "token"},
            "accounts": {"normal_user": {"username": "test_user", "password": "<FILL>"}},
        },
        "validation_steps": ["Configure auth_flow in Settings", "Add test accounts", "Verify auth_session_ready passes"],
        "estimated_effort": "hours",
    },
    GapRootCause.MISSING_OPENAPI: {
        "title": "Upload API specification (OpenAPI/Swagger)",
        "description": "Without an API spec, the system can only produce PRD-based candidate clues — no executable API probes.",
        "config_keys": ["source_assets"],
        "config_example": {},
        "validation_steps": ["Upload OpenAPI 3.0 JSON/YAML in Materials", "Confirm source type shows 'api_spec'", "Rerun preflight"],
        "estimated_effort": "minutes",
    },
    GapRootCause.MISSING_TEST_ACCOUNTS: {
        "title": "Add role coverage (normal/admin/owner/cross-tenant)",
        "description": "Boundary and tenant-isolation probes are weaker without multi-role test accounts.",
        "config_keys": ["accounts.normal_user", "accounts.admin_user", "accounts.owner_user", "accounts.cross_tenant_user"],
        "config_example": {
            "accounts": {
                "normal_user": {"username": "<FILL>", "password": "<FILL>", "tenant_id": "<FILL>"},
                "admin_user": {"username": "<FILL>", "password": "<FILL>", "tenant_id": "<FILL>"},
            },
        },
        "validation_steps": ["Add missing roles in Settings > Accounts", "Rerun preflight", "Confirm role_coverage has no missing roles"],
        "estimated_effort": "hours",
    },
    GapRootCause.PRODUCTION_TARGET: {
        "title": "Switch to non-production target",
        "description": "QualiBug must not run probes against production URLs. Use a dedicated staging/QA environment.",
        "config_keys": ["base_url", "environment_kind"],
        "config_example": {"base_url": "https://staging.example.com", "environment_kind": "staging"},
        "validation_steps": ["Change base_url to staging", "Ensure non_production_target passes"],
        "estimated_effort": "minutes",
    },
    GapRootCause.WRITE_SANDBOX_MISSING: {
        "title": "Enable sandbox write execution",
        "description": "High-value write probes need QualiBug-created qb_auto_* data in an isolated sandbox.",
        "config_keys": ["test_environment.enabled", "test_environment.allow_write_probes", "auto_fixture.enabled"],
        "config_example": {"test_environment": {"enabled": True, "allow_write_probes": True}, "auto_fixture": {"enabled": True}},
        "validation_steps": ["Enable sandbox in Settings", "Request sandbox approval if required", "Rerun preflight"],
        "estimated_effort": "hours",
    },
    GapRootCause.CLEANUP_MISSING: {
        "title": "Declare cleanup/reset strategy",
        "description": "Before/after write probes must leave the staging environment clean and repeatable.",
        "config_keys": ["test_environment.cleanup_strategy"],
        "config_example": {"test_environment": {"cleanup_strategy": "fixture_reset"}},
        "validation_steps": ["Configure cleanup_strategy", "Rerun preflight", "Confirm cleanup_health_declared passes"],
        "estimated_effort": "hours",
    },
    GapRootCause.SNAPSHOT_MISSING: {
        "title": "Expose read-only snapshot observers",
        "description": "P0/P1 runtime validation needs before/after detail views to prove side effects.",
        "config_keys": ["snapshots"],
        "config_example": {"snapshots": {"*": {"before": [{"method": "GET", "path": "/api/items/{id}"}]}}},
        "validation_steps": ["Configure snapshot observer endpoints", "Rerun preflight", "Confirm snapshot_observer_ready passes"],
        "estimated_effort": "hours",
    },
    GapRootCause.CONFIG_PLACEHOLDERS: {
        "title": "Replace template placeholders with real values",
        "description": "Generated config templates contain <FILL:...> placeholders that must be replaced before execution.",
        "config_keys": ["probe_config"],
        "config_example": {},
        "validation_steps": ["Replace all <FILL:...> placeholders with staging values", "Rerun preflight"],
        "estimated_effort": "minutes",
    },
    GapRootCause.BROWSER_DISABLED: {
        "title": "Enable browser-based testing",
        "description": "UI/UX defect families require browser automation. Enable browser UI smoke testing.",
        "config_keys": ["browser_ui.enabled"],
        "config_example": {"browser_ui": {"enabled": True, "headless": True}},
        "validation_steps": ["Install browser dependencies", "Enable browser_ui in Settings", "Verify browser health check passes"],
        "estimated_effort": "hours",
    },
    GapRootCause.TESTOPS_OFFLINE: {
        "title": "Enable Enterprise TestOps",
        "description": "Compatibility and configuration drift families need TestOps control plane.",
        "config_keys": ["enterprise_testops.enabled"],
        "config_example": {"enterprise_testops": {"enabled": True}},
        "validation_steps": ["Configure TestOps connection", "Verify TestOps health check passes"],
        "estimated_effort": "hours",
    },
    GapRootCause.NO_CREDENTIALS: {
        "title": "Configure service credentials",
        "description": "No service credentials configured. Scans cannot authenticate to the target system.",
        "config_keys": ["multi_service_config"],
        "config_example": {"services": [{"name": "default", "base_url": "https://staging.example.com"}]},
        "validation_steps": ["Save service credentials in Settings", "Rerun preflight"],
        "estimated_effort": "minutes",
    },
    GapRootCause.NO_SOURCE: {
        "title": "Upload source materials (PRD, OpenAPI, etc.)",
        "description": "No knowledge assets uploaded. Upload at minimum a PRD or OpenAPI spec.",
        "config_keys": ["source_assets"],
        "config_example": {},
        "validation_steps": ["Upload PRD.md or openapi.json in Materials", "Confirm source is registered"],
        "estimated_effort": "minutes",
    },
    GapRootCause.NO_API_SPEC: {
        "title": "Upload API specification for executable probes",
        "description": "Sources exist but no API spec. The scan can only produce PRD-based candidate clues.",
        "config_keys": ["source_assets (OpenAPI)"],
        "config_example": {},
        "validation_steps": ["Upload OpenAPI/Swagger/Postman spec in Materials"],
        "estimated_effort": "minutes",
    },
}


# ═════════════════════════════════════════════════════════════════════════════
# Defect Family → Required Capabilities Mapping
# ═════════════════════════════════════════════════════════════════════════════

FAMILY_REQUIRED_CAPABILITIES: dict[str, list[str]] = {
    "scenario_flow": ["runtime_http_probes", "api_contract_acceptance"],
    "api_contract": ["api_contract_acceptance"],
    "security_boundary": ["runtime_http_probes", "security_boundary_probes"],
    "privacy_compliance": ["runtime_http_probes", "api_contract_acceptance"],
    "observability": ["api_contract_acceptance"],
    "configuration_drift": ["differential_tests"],
    "data_integrity": ["runtime_http_probes"],
    "performance": ["runtime_http_probes"],
    "stability": ["runtime_http_probes"],
    "compatibility": ["differential_tests"],
    "ui": ["browser_ui_replay"],
    "uiux": ["browser_ui_replay"],
    "accessibility_i18n": ["browser_ui_replay", "differential_tests"],
}


# ═════════════════════════════════════════════════════════════════════════════
# Capability Gap Resolver
# ═════════════════════════════════════════════════════════════════════════════

class CapabilityGapResolver:
    """Unified resolver for plan_only capability gaps.

    Usage::

        resolver = CapabilityGapResolver()
        gaps = resolver.detect_from_preflight(preflight_result, capability_matrix)
        tasks = resolver.generate_config_tasks(gaps)
        report = resolver.build_gap_report(gaps)
    """

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        self._log: list[str] = []

    # ── Gap Detection ──────────────────────────────────────────────────

    def detect_from_preflight(
        self,
        preflight: dict[str, Any],
        capability_matrix: dict[str, Any] | None = None,
    ) -> list[CapabilityGap]:
        """Detect all capability gaps from preflight check results.

        Args:
            preflight: Output from runtime_onboarding_preflight.run_runtime_onboarding_preflight().
            capability_matrix: Optional output from full_spectrum_capability_matrix.

        Returns:
            List of CapabilityGap objects, one per unique root cause.
        """
        gaps: list[CapabilityGap] = []
        seen_causes: set[GapRootCause] = set()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        checks = preflight.get("checks") or []

        for check in checks:
            if not isinstance(check, dict):
                continue
            if check.get("ok") or check.get("status") == "passed":
                continue

            name = str(check.get("name") or "")
            root_cause = CHECK_TO_ROOT_CAUSE.get(name, GapRootCause.UNKNOWN)

            if root_cause in seen_causes:
                # Merge into existing gap
                for g in gaps:
                    if g.root_cause == root_cause:
                        g.preflight_check_names.append(name)
                        g.affected_probe_count = max(
                            g.affected_probe_count,
                            check.get("affected_probe_count", 0),
                        )
                        break
                continue

            seen_causes.add(root_cause)
            resolution = ROOT_CAUSE_RESOLUTION.get(root_cause, GapResolution.NEEDS_CUSTOMER_CONFIG)

            # Determine severity from check
            severity = str(check.get("severity") or "P2")
            if severity == "blocking":
                severity = "P0"
            elif severity == "warning":
                severity = "P1"

            # Determine affected families from capability matrix
            affected_families = self._affected_families_for_cause(root_cause)

            # Build config task from template
            task_template = CONFIG_TASK_TEMPLATES.get(root_cause)
            config_task = None
            if task_template:
                config_task = ConfigTask(
                    task_id=f"CFG-{root_cause.value}",
                    priority=severity,
                    title=task_template["title"],
                    description=task_template["description"],
                    config_keys=list(task_template.get("config_keys", [])),
                    config_example=dict(task_template.get("config_example", {})),
                    validation_steps=list(task_template.get("validation_steps", [])),
                    estimated_effort=task_template.get("estimated_effort", "hours"),
                )

            gap = CapabilityGap(
                gap_id=f"GAP-{self.project_id}-{root_cause.value}-{int(time.time())}",
                root_cause=root_cause,
                resolution=resolution,
                priority=severity,
                summary=check.get("message") or str(root_cause.value),
                affected_defect_families=affected_families,
                affected_probe_count=check.get("affected_probe_count", 0),
                affected_capability_ids=self._capability_ids_for_cause(root_cause),
                preflight_check_names=[name],
                config_task=config_task,
                detected_at=timestamp,
                metadata={"check_status": check.get("status"), "check_severity": check.get("severity")},
            )
            gaps.append(gap)

        # Also detect from capability matrix if provided
        if capability_matrix:
            matrix_gaps = self._detect_from_matrix(capability_matrix, timestamp)
            for mg in matrix_gaps:
                if mg.root_cause not in seen_causes:
                    seen_causes.add(mg.root_cause)
                    gaps.append(mg)

        # Also add gaps from scan-level preflight reasons
        reasons = preflight.get("reasons") or []
        for reason in reasons:
            if not isinstance(reason, dict):
                continue
            code = str(reason.get("code") or "")
            root_cause = SCAN_REASON_TO_ROOT_CAUSE.get(code, GapRootCause.UNKNOWN)
            if root_cause in seen_causes:
                continue
            seen_causes.add(root_cause)
            resolution = ROOT_CAUSE_RESOLUTION.get(root_cause, GapResolution.NEEDS_CUSTOMER_CONFIG)
            task_template = CONFIG_TASK_TEMPLATES.get(root_cause)
            config_task = None
            if task_template:
                config_task = ConfigTask(
                    task_id=f"CFG-{root_cause.value}",
                    priority="P1",
                    title=task_template["title"],
                    description=task_template["description"],
                    config_keys=list(task_template.get("config_keys", [])),
                    config_example=dict(task_template.get("config_example", {})),
                    validation_steps=list(task_template.get("validation_steps", [])),
                    estimated_effort=task_template.get("estimated_effort", "hours"),
                )
            gaps.append(CapabilityGap(
                gap_id=f"GAP-{self.project_id}-{root_cause.value}-{int(time.time())}",
                root_cause=root_cause,
                resolution=resolution,
                priority="P1",
                summary=reason.get("message", str(root_cause.value)),
                affected_defect_families=self._affected_families_for_cause(root_cause),
                preflight_check_names=[],
                config_task=config_task,
                detected_at=timestamp,
                metadata={"reason_code": code},
            ))

        self._log.append(
            f"detect_from_preflight: {len(gaps)} gaps from {len(checks)} checks, "
            f"root_causes={[g.root_cause.value for g in gaps]}"
        )
        return gaps

    def _detect_from_matrix(
        self, matrix: dict[str, Any], timestamp: str
    ) -> list[CapabilityGap]:
        """Detect gaps from the full-spectrum capability matrix rows."""
        gaps: list[CapabilityGap] = []
        rows = matrix.get("rows") or []

        for row in rows:
            if not isinstance(row, dict):
                continue
            lane = str(row.get("preflight_lane") or "")
            if lane in ("capability_ready", "source_ready"):
                continue

            # Determine root cause from missing capabilities
            missing = list(row.get("missing_blocking_capabilities") or []) + list(
                row.get("missing_optional_capabilities") or []
            )
            for cap in missing:
                root_cause = self._root_cause_for_capability(str(cap))
                gaps.append(CapabilityGap(
                    gap_id=f"GAP-MATRIX-{root_cause.value}-{int(time.time())}",
                    root_cause=root_cause,
                    resolution=ROOT_CAUSE_RESOLUTION.get(root_cause, GapResolution.NEEDS_CUSTOMER_CONFIG),
                    priority="P1",
                    summary=f"Capability '{cap}' missing: lane={lane}",
                    affected_capability_ids=[str(cap)],
                    preflight_check_names=[],
                    detected_at=timestamp,
                ))

        return gaps

    def detect_from_scan_preflight(
        self, reasons: list[dict[str, str]]
    ) -> list[CapabilityGap]:
        """Detect gaps from scan preflight reasons (NO_CREDENTIALS, NO_API_SPEC, etc.)."""
        gaps: list[CapabilityGap] = []
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        for reason in reasons:
            if not isinstance(reason, dict):
                continue
            code = str(reason.get("code") or "")
            root_cause = SCAN_REASON_TO_ROOT_CAUSE.get(code, GapRootCause.UNKNOWN)
            resolution = ROOT_CAUSE_RESOLUTION.get(root_cause, GapResolution.NEEDS_CUSTOMER_CONFIG)
            task_template = CONFIG_TASK_TEMPLATES.get(root_cause)
            config_task = None
            if task_template:
                config_task = ConfigTask(
                    task_id=f"CFG-{root_cause.value}",
                    priority="P1",
                    title=task_template["title"],
                    description=task_template["description"],
                    config_keys=list(task_template.get("config_keys", [])),
                    config_example=dict(task_template.get("config_example", {})),
                    validation_steps=list(task_template.get("validation_steps", [])),
                    estimated_effort=task_template.get("estimated_effort", "hours"),
                )

            gaps.append(CapabilityGap(
                gap_id=f"GAP-SCAN-{root_cause.value}-{int(time.time())}",
                root_cause=root_cause,
                resolution=resolution,
                priority="P1",
                summary=reason.get("message", str(root_cause.value)),
                affected_defect_families=self._affected_families_for_cause(root_cause),
                config_task=config_task,
                detected_at=timestamp,
                metadata={"reason_code": code},
            ))

        self._log.append(f"detect_from_scan_preflight: {len(gaps)} gaps from {len(reasons)} reasons")
        return gaps

    # ── Gap Classification ─────────────────────────────────────────────

    @staticmethod
    def classify_gap(gap: CapabilityGap) -> GapResolution:
        """Classify a gap's resolution type."""
        return ROOT_CAUSE_RESOLUTION.get(gap.root_cause, GapResolution.NEEDS_CUSTOMER_CONFIG)

    def get_auto_resolvable_gaps(self, gaps: list[CapabilityGap]) -> list[CapabilityGap]:
        """Filter gaps that can be auto-resolved."""
        return [g for g in gaps if g.resolution == GapResolution.AUTO_RESOLVABLE]

    def get_blocking_gaps(self, gaps: list[CapabilityGap]) -> list[CapabilityGap]:
        """Filter gaps that block execution (P0 priority)."""
        return [g for g in gaps if g.priority == "P0"]

    # ── Task Generation ────────────────────────────────────────────────

    def generate_config_tasks(self, gaps: list[CapabilityGap]) -> list[ConfigTask]:
        """Generate concrete configuration tasks from detected gaps."""
        tasks: list[ConfigTask] = []
        seen: set[str] = set()

        for gap in gaps:
            if gap.config_task and gap.config_task.task_id not in seen:
                seen.add(gap.config_task.task_id)
                tasks.append(gap.config_task)

        # Sort by priority
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        tasks.sort(key=lambda t: priority_order.get(t.priority, 2))
        return tasks

    # ── Auto-Resolution ────────────────────────────────────────────────

    def try_auto_resolve(
        self,
        gap: CapabilityGap,
        current_preflight: dict[str, Any],
    ) -> ResolutionResult:
        """Try to auto-resolve a gap based on current preflight status.

        Only AUTO_RESOLVABLE gaps can be auto-resolved. For others, this
        returns a result indicating what's needed.

        Args:
            gap: The gap to try resolving.
            current_preflight: Latest preflight check results.

        Returns:
            ResolutionResult indicating success or what's still needed.
        """
        if gap.resolution != GapResolution.AUTO_RESOLVABLE:
            return ResolutionResult(
                gap_id=gap.gap_id,
                resolved=False,
                reason=f"Gap '{gap.root_cause.value}' requires '{gap.resolution.value}', cannot auto-resolve",
            )

        # Check if the corresponding preflight checks now pass
        all_passed = True
        for check_name in gap.preflight_check_names:
            check = self._find_check(current_preflight, check_name)
            if not check or not check.get("ok"):
                all_passed = False
                break

        if all_passed:
            return ResolutionResult(
                gap_id=gap.gap_id,
                resolved=True,
                reason=f"All preflight checks now pass: {gap.preflight_check_names}",
                promoted_probe_count=gap.affected_probe_count,
                promoted_family_count=len(gap.affected_defect_families),
            )

        return ResolutionResult(
            gap_id=gap.gap_id,
            resolved=False,
            reason=f"Preflight checks still failing: {gap.preflight_check_names}",
            details={"failing_checks": gap.preflight_check_names},
        )

    def re_evaluate_all(
        self,
        gaps: list[CapabilityGap],
        current_preflight: dict[str, Any],
    ) -> list[ResolutionResult]:
        """Re-evaluate all gaps against current preflight status."""
        results: list[ResolutionResult] = []
        for gap in gaps:
            result = self.try_auto_resolve(gap, current_preflight)
            results.append(result)
        resolved = sum(1 for r in results if r.resolved)
        self._log.append(
            f"re_evaluate_all: {len(gaps)} gaps, {resolved} resolved, "
            f"{len(gaps) - resolved} still open"
        )
        return results

    # ── Gap Report ─────────────────────────────────────────────────────

    def build_gap_report(self, gaps: list[CapabilityGap]) -> dict[str, Any]:
        """Build a JSON-safe gap summary for dashboard / scan output."""
        by_resolution: dict[str, int] = {}
        by_root_cause: dict[str, int] = {}
        p0_count = 0
        p1_count = 0

        for gap in gaps:
            res_key = gap.resolution.value
            by_resolution[res_key] = by_resolution.get(res_key, 0) + 1
            cause_key = gap.root_cause.value
            by_root_cause[cause_key] = by_root_cause.get(cause_key, 0) + 1
            if gap.priority == "P0":
                p0_count += 1
            elif gap.priority == "P1":
                p1_count += 1

        tasks = self.generate_config_tasks(gaps)

        return {
            "schema_version": "capability_gap_report.v1",
            "project_id": self.project_id,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_gaps": len(gaps),
            "p0_gaps": p0_count,
            "p1_gaps": p1_count,
            "by_resolution_type": by_resolution,
            "by_root_cause": by_root_cause,
            "auto_resolvable": by_resolution.get("auto_resolvable", 0),
            "needs_customer_config": by_resolution.get("needs_customer_config", 0),
            "needs_approval": by_resolution.get("needs_approval", 0),
            "needs_infra": by_resolution.get("needs_infra", 0),
            "permanently_blocked": by_resolution.get("permanently_blocked", 0),
            "config_tasks": [
                {
                    "task_id": t.task_id,
                    "priority": t.priority,
                    "title": t.title,
                    "config_keys": t.config_keys,
                    "validation_steps": t.validation_steps,
                }
                for t in tasks
            ],
            "gaps": [
                {
                    "gap_id": g.gap_id,
                    "root_cause": g.root_cause.value,
                    "resolution": g.resolution.value,
                    "priority": g.priority,
                    "summary": g.summary,
                    "affected_families": g.affected_defect_families,
                    "affected_probe_count": g.affected_probe_count,
                }
                for g in gaps
            ],
        }

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _find_check(preflight: dict[str, Any], name: str) -> dict[str, Any] | None:
        for item in preflight.get("checks") or []:
            if isinstance(item, dict) and item.get("name") == name:
                return item
        return None

    @staticmethod
    def _affected_families_for_cause(cause: GapRootCause) -> list[str]:
        """Determine which defect families are affected by a root cause."""
        affected: set[str] = set()

        # Map root causes to capability IDs they block
        cause_to_cap: dict[GapRootCause, list[str]] = {
            GapRootCause.MISSING_BASE_URL: ["runtime_http_probes", "security_boundary_probes"],
            GapRootCause.MISSING_AUTH: ["runtime_http_probes", "security_boundary_probes"],
            GapRootCause.MISSING_OPENAPI: ["api_contract_acceptance", "property_fuzz_lite"],
            GapRootCause.MISSING_TEST_ACCOUNTS: ["security_boundary_probes"],
            GapRootCause.PRODUCTION_TARGET: ["runtime_http_probes", "security_boundary_probes", "browser_ui_replay"],
            GapRootCause.BROWSER_DISABLED: ["browser_ui_replay"],
            GapRootCause.TESTOPS_OFFLINE: ["differential_tests"],
            GapRootCause.WRITE_SANDBOX_MISSING: ["runtime_http_probes"],
            GapRootCause.CLEANUP_MISSING: ["runtime_http_probes"],
            GapRootCause.SNAPSHOT_MISSING: ["runtime_http_probes"],
            GapRootCause.NO_CREDENTIALS: ["runtime_http_probes", "security_boundary_probes"],
            GapRootCause.NO_SOURCE: ["api_contract_acceptance"],
            GapRootCause.NO_API_SPEC: ["api_contract_acceptance", "runtime_http_probes"],
        }

        blocked_caps = cause_to_cap.get(cause, [])
        for family, required_caps in FAMILY_REQUIRED_CAPABILITIES.items():
            if any(cap in blocked_caps for cap in required_caps):
                affected.add(family)

        return sorted(affected) if affected else ["scenario_flow"]  # Default to most common

    @staticmethod
    def _capability_ids_for_cause(cause: GapRootCause) -> list[str]:
        """Return the capability IDs blocked by a root cause."""
        mapping: dict[GapRootCause, list[str]] = {
            GapRootCause.MISSING_BASE_URL: ["runtime_http_probes", "security_boundary_probes"],
            GapRootCause.MISSING_AUTH: ["runtime_http_probes", "security_boundary_probes"],
            GapRootCause.MISSING_OPENAPI: ["api_contract_acceptance"],
            GapRootCause.BROWSER_DISABLED: ["browser_ui_replay"],
            GapRootCause.TESTOPS_OFFLINE: ["differential_tests"],
            GapRootCause.WRITE_SANDBOX_MISSING: ["runtime_http_probes"],
        }
        return mapping.get(cause, [])

    @staticmethod
    def _root_cause_for_capability(capability_name: str) -> GapRootCause:
        """Map a capability name to its most likely root cause."""
        mapping: dict[str, GapRootCause] = {
            "base_url_configured": GapRootCause.MISSING_BASE_URL,
            "base_url_reachable": GapRootCause.MISSING_BASE_URL,
            "non_production_target": GapRootCause.PRODUCTION_TARGET,
            "probe_plan_grounded": GapRootCause.DOCUMENT_GROUNDING,
            "auth_session_ready": GapRootCause.MISSING_AUTH,
            "role_coverage": GapRootCause.MISSING_TEST_ACCOUNTS,
            "write_sandbox": GapRootCause.WRITE_SANDBOX_MISSING,
            "cleanup": GapRootCause.CLEANUP_MISSING,
            "snapshot_observer": GapRootCause.SNAPSHOT_MISSING,
            "config_placeholders_resolved": GapRootCause.CONFIG_PLACEHOLDERS,
            "browser_ready": GapRootCause.BROWSER_DISABLED,
            "testops_ready": GapRootCause.TESTOPS_OFFLINE,
            "openapi_parse": GapRootCause.MISSING_OPENAPI,
        }
        return mapping.get(capability_name, GapRootCause.UNKNOWN)

    def get_log(self) -> list[str]:
        return list(self._log)
