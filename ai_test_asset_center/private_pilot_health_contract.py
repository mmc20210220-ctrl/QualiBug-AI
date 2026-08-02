from __future__ import annotations

"""Health contract for patched private-pilot deployments."""

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from ai_test_asset_center.version import (
    CANONICAL_HEALTH_PATH,
    DEFAULT_PRIVATE_PILOT_PORT,
    LEGACY_HEALTH_PATH,
    PRODUCT_CHANNEL,
    PRODUCT_NAME,
    PRODUCT_PHASE,
    PRODUCT_VERSION,
)


def int_env(name: str, fallback: int) -> int:
    try:
        return int(os.environ.get(name, "") or fallback)
    except Exception:
        return fallback


def pattern_library_count(root: Path) -> int:
    for candidate in (
        root / "pattern_library" / "patterns.json",
        root / "platform_workspace" / "pattern_library" / "patterns.json",
    ):
        try:
            if candidate.exists():
                data = json.loads(candidate.read_text(encoding="utf-8") or "{}")
                patterns = data.get("patterns") if isinstance(data, dict) else []
                return len(patterns) if isinstance(patterns, list) else 0
        except Exception:
            continue
    return 0


def browser_ui_status() -> dict[str, Any]:
    try:
        from ai_test_asset_center.browser_ui_smoke import is_browser_ui_enabled

        enabled = is_browser_ui_enabled()
    except Exception:
        enabled = False
    return {
        "enabled": enabled,
        "env_flag": "QUALIBUG_BROWSER_UI_SMOKE",
        "mode": "smoke" if enabled else "disabled",
        "evidence": ["page_reachability", "console_errors", "network_errors", "screenshots", "har"],
    }


def system_behavior_runtime_status() -> dict[str, Any]:
    """Expose whether the existing runtime chain is patched end-to-end.

    This is a health/readiness manifest, not a new execution path.  It inspects
    the existing modules that are patched by ``private_pilot_entrypoint`` so a
    private deployment can verify the System Behavior Space loop is actually on.
    """
    checks: dict[str, bool] = {}
    sources: dict[str, str] = {}
    versions: dict[str, str] = {}
    try:
        from ai_test_asset_center import business_state_graph as _bsg
        checks["behavior_contract"] = bool(
            getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCHED", False)
            or getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_MODE", "") == "first_class_hook"
        )
        sources["behavior_contract"] = str(
            getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_MODE", "")
            or getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE", "")
            or ""
        )
    except Exception:
        checks["behavior_contract"] = False
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        from ai_test_asset_center.system_behavior_space_context import (
            FIRST_CLASS_CONTEXT_BINDER,
        )

        # First-class binder inside run_v12_pipeline satisfies readiness even
        # before install_runtime_patches marks the compatibility flag.
        checks["v12_project_context"] = bool(
            FIRST_CLASS_CONTEXT_BINDER
            or getattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED", False)
        )
        checks["confirmed_finding_contract"] = bool(
            getattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_PATCHED", False)
            or getattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_MODE", "") == "first_class_hook"
        )
        checks["coverage_steering"] = bool(
            getattr(_v12, "_COVERAGE_STEERING_PATCHED", False)
            or getattr(_v12, "_COVERAGE_STEERING_MODE", "") == "first_class_hook"
        )
        sources["coverage_steering"] = str(
            getattr(_v12, "_COVERAGE_STEERING_PATCH_SOURCE", "") or ""
        )
        sources["v12_project_context"] = str(
            getattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_MODE", "")
            or ("first_class" if FIRST_CLASS_CONTEXT_BINDER else "")
        )
        sources["confirmed_finding_contract"] = str(
            getattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_MODE", "") or ""
        )
    except Exception:
        checks["v12_project_context"] = False
        checks["confirmed_finding_contract"] = False
        checks["coverage_steering"] = False
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
        checks["scenario_runtime_hints"] = bool(
            getattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_PATCHED", False)
            or getattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_MODE", "") == "first_class_hook"
        )
        sources["scenario_runtime_hints"] = str(
            getattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_MODE", "") or ""
        )
    except Exception:
        checks["scenario_runtime_hints"] = False
    try:
        from ai_test_asset_center import oracle_engine as _oe
        checks["oracle_evidence_linkage"] = bool(
            getattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_PATCHED", False)
            or getattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_MODE", "") == "first_class_hook"
        )
        sources["oracle_evidence_linkage"] = str(
            getattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_MODE", "") or ""
        )
    except Exception:
        checks["oracle_evidence_linkage"] = False
    try:
        from ai_test_asset_center import regression_runner as _rr
        checks["regression_contract_replay"] = bool(
            getattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_PATCHED", False)
            or getattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_MODE", "") == "first_class_hook"
        )
        sources["regression_contract_replay"] = str(
            getattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_MODE", "") or ""
        )
    except Exception:
        checks["regression_contract_replay"] = False
    try:
        from ai_test_asset_center import risk_clue_pool as _risk
        versions["risk_clue_pool_project_learning"] = str(getattr(_risk, "PROJECT_LEARNING_VERSION", "") or "")
        versions["risk_clue_pool_platform_learning"] = str(getattr(_risk, "PLATFORM_LEARNING_VERSION", "") or "")
    except Exception:
        versions["risk_clue_pool_project_learning"] = ""
        versions["risk_clue_pool_platform_learning"] = ""
    try:
        from ai_test_asset_center.system_behavior_space import SYSTEM_BEHAVIOR_SPACE_VERSION
        versions["system_behavior_space"] = SYSTEM_BEHAVIOR_SPACE_VERSION
    except Exception:
        versions["system_behavior_space"] = ""

    required = [
        "behavior_contract",
        "v12_project_context",
        "scenario_runtime_hints",
        "oracle_evidence_linkage",
        "confirmed_finding_contract",
        "regression_contract_replay",
        "coverage_steering",
    ]
    ready = all(bool(checks.get(key)) for key in required)
    return {
        "ready": ready,
        "mode": "system_promise_discovery_loop" if ready else "partially_installed",
        "checks": checks,
        "sources": sources,
        "versions": versions,
        "data_boundary": {
            "project_learning_scope": "private_deployment",
            "platform_learning_scope": "sanitized_pattern_weights_only",
            "raw_customer_data_in_platform_learning": False,
        },
        "chain": [
            "enterprise_knowledge_asset",
            "system_behavior_space",
            "behavior_contract_slices",
            "semantic_scenarios",
            "oracle_evidence",
            "confirmed_findings_ledger",
            "regression_contract_replay",
            "risk_clue_pool_learning",
            "coverage_learning_steering",
        ],
    }


def _handler_root(handler: Any, fallback_root: Path) -> Path:
    try:
        return handler._root()
    except Exception:
        return fallback_root


def _handler_llm_health(handler: Any) -> dict[str, Any]:
    try:
        return handler._llm_health()
    except Exception as exc:
        return {
            "available": False,
            "status": "offline",
            "label": "offline",
            "error": str(exc)[:300],
        }


def build_private_pilot_health_payload(
    handler: Any,
    *,
    fallback_root: Path,
    patch_source: str,
) -> dict[str, Any]:
    root = _handler_root(handler, fallback_root)
    llm_health = _handler_llm_health(handler)
    root_ok = root.exists()
    system_behavior = system_behavior_runtime_status()
    api_status = "healthy"  # serving this response proves API liveness only
    llm_available = bool(llm_health.get("available"))
    llm_status = str(llm_health.get("status") or "offline")
    sb_ready = bool(system_behavior.get("ready"))
    sb_status = "healthy" if sb_ready else "degraded"

    # Liveness and business readiness are different deployment facts. The
    # process can be alive before an operator configures an LLM or imports
    # enterprise sources; reporting that state as HTTP 503 makes orchestrators
    # restart a healthy process and prevents configuration through the product.
    offline_reasons: list[str] = []
    readiness_blockers: list[str] = []
    degraded_reasons: list[str] = []
    if not root_ok:
        offline_reasons.append("private_root_missing")
    if not llm_available or llm_status in {"offline", "failed", "error"}:
        readiness_blockers.append("llm_offline")
    if not sb_ready:
        readiness_blockers.append("system_behavior_not_ready")

    live = not offline_reasons
    ready = live and not readiness_blockers
    if not live:
        overall_status = "offline"
    elif not ready:
        overall_status = "degraded"
        degraded_reasons.extend(readiness_blockers)
    else:
        overall_status = "healthy"
    return {
        # Compatibility: ``ok`` answers whether the service can serve requests.
        # Readiness is exposed independently and must be used for real scans.
        "ok": live,
        "status": overall_status,
        "live": live,
        "ready": ready,
        "readiness_status": "ready" if ready else "blocked",
        "readiness_blockers": readiness_blockers,
        "offline_reasons": offline_reasons,
        "degraded_reasons": degraded_reasons,
        "service": "qualibug_private_pilot",
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "product_version": PRODUCT_VERSION,
        "phase": PRODUCT_PHASE,
        "channel": PRODUCT_CHANNEL,
        "api_version": "v1",
        "private_root": str(root),
        "private_root_exists": root_ok,
        "public_bind_allowed": os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1",
        "bind_host": os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1"),
        "port": int_env("QUALIBUG_PORT", DEFAULT_PRIVATE_PILOT_PORT),
        "canonical_health_path": CANONICAL_HEALTH_PATH,
        "legacy_health_path": LEGACY_HEALTH_PATH,
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        "llm_available": bool(llm_health.get("available")),
        "llm_status": llm_health,
        "browser_ui_smoke": browser_ui_status(),
        "pattern_library_patterns": pattern_library_count(root),
        "system_behavior_runtime": system_behavior,
        "deployment_contract_patch": {
            "patched": True,
            "source": patch_source,
            "port_contract": f"container:{DEFAULT_PRIVATE_PILOT_PORT}",
            "health_contract": CANONICAL_HEALTH_PATH,
        },
        "components": {
            "api": {
                "status": api_status,
                "service": "qualibug_private_pilot",
                "root_exists": root_ok,
            },
            "llm": {
                "status": llm_status,
                "available": llm_available,
            },
            "system_behavior": {
                "status": sb_status,
                "ready": sb_ready,
            },
            "private_root": {
                "status": "healthy" if root_ok else "offline",
                "exists": root_ok,
                "path": str(root),
            },
        },
    }
