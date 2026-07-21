"""real_project_defect_discovery package - backward-compatible facade.

All public and private symbols are re-exported so that existing
``from .real_project_defect_discovery import X`` continues to work.
"""
from ._common import *  # noqa: F401,F403
from ._helpers import *  # noqa: F401,F403
from ._runner import *  # noqa: F401,F403
from ._reporting import *  # noqa: F401,F403

# Explicit re-exports for underscore-prefixed symbols
from ._common import _BROWSER_UI_BLOCKED_SOURCES  # noqa: F401
from ._helpers import _live_mode_or_plan, _fetch_json_or_text, _apply_browser_health_probe_policy, _augment_risk_plan_with_browser_health, _extract_token, _login, _path_keywords, _load_enterprise_history_patterns, _history_pattern_matches_path, _status_suspicious, _append_adapter_issue, _top_reason_rows, _safe_rate, _build_low_discovery_diagnosis, _execution_attempted, _strict_verifier_for_issue, _build_discovery_funnel  # noqa: F401
from ._reporting import _impact_for_risk, _fix_for_risk, _render_bug_drafts  # noqa: F401

__all__ = [
    "DESTRUCTIVE_RISK_TYPES",
    "_BROWSER_UI_BLOCKED_SOURCES",
    "_live_mode_or_plan",
    "_fetch_json_or_text",
    "_apply_browser_health_probe_policy",
    "_augment_risk_plan_with_browser_health",
    "_extract_token",
    "_login",
    "_path_keywords",
    "_load_enterprise_history_patterns",
    "_history_pattern_matches_path",
    "generate_history_informed_probes",
    "generate_real_project_probes",
    "_status_suspicious",
    "_append_adapter_issue",
    "_top_reason_rows",
    "_safe_rate",
    "_build_low_discovery_diagnosis",
    "_execution_attempted",
    "_strict_verifier_for_issue",
    "_build_discovery_funnel",
    "run_real_project_discovery",
    "_impact_for_risk",
    "_fix_for_risk",
    "_render_bug_drafts",
    "render_real_project_report",
    "main",
]
