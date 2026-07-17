from __future__ import annotations

"""Private-network HTTP entrypoint for the QualiBug pilot runtime.

The service binds to localhost by default. In private-cloud deployments, a
trusted reverse proxy or enterprise SSO gateway should authenticate users and
inject the actor/role headers documented below. The service never accepts raw
credential values; connectors only receive secret references.

Handler behavior lives in focused mixins; this module remains the composition
root and compatibility re-export surface.
"""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import db_persistence as db_persist  # noqa: F401
from .customer_delivery_gate import split_customer_delivery_tracks as _partition_delivery_tracks

from .private_pilot_json_io import (  # noqa: F401
    _read_json_artifact,
    _read_json_object,
    _read_json_safe,
    _write_json_object_atomic,
)
from .private_pilot_debug_client import (  # noqa: F401
    _dbg_env,
    _dbg_fingerprint_payload,
    _dbg_report,
)
from .private_pilot_project_assets import (  # noqa: F401
    KNOWLEDGE_INGEST_BINARY_EXTENSIONS,
    KNOWLEDGE_INGEST_EXTENSIONS,
    KNOWLEDGE_INGEST_SOURCE_TYPES,
    KNOWLEDGE_INGEST_TEXT_EXTENSIONS,
    MASKED_CREDENTIAL_VALUE,
    ONBOARD_DOCUMENT_EXTENSIONS,
    ONBOARD_OPENAPI_EXTENSIONS,
    _credential_update_value,
    _extensions_accept,
    _extensions_label,
    _first_text,
    _is_masked_credential_value,
    _knowledge_asset_sources,
    _known_project_exists,
    _load_real_project_discovery_payload,
    _normalize_frontend_page_path,
    _project_output_dir_for_import,
    _root,
    _truthy_env,
    _write_env_local,
)
from .private_pilot_tenant_auth import (  # noqa: F401
    PROJECT_SCOPE_HEADER,
    TenantAuthenticationError,
    _actor,
    _current_tenant,
    _parse_project_scopes,
    _tenant_from_headers,
)
from .private_pilot_campaign_projection import (  # noqa: F401
    _augment_continuous_discovery_campaign,
    _current_campaign_bundle_finding_stats,
    _current_campaign_scope_summary,
    _report_finding_dedupe_key as _finding_dedupe_key,
)
from .private_pilot_scan_aggregates import (  # noqa: F401
    _extend_stage3_impact_analysis,
    _synchronize_scan_aggregates,
)
from .private_pilot_regression_projection import (  # noqa: F401
    _build_regression_release_guidance,
    _build_regression_validation_summary,
    _load_regression_history,
    _load_regression_projection,
    _regression_lifecycle,
    _regression_lookup_keys,
    _regression_status_label,
    _regression_summary_title,
    _regression_trend_direction,
)
from .private_pilot_defect_summaries import (  # noqa: F401
    _build_defect_delivery_cards,
    _build_defect_grouped_summary,
    _build_defect_priority_summary,
    _build_defect_repro_summary,
    _claim_request_identity,
    _extract_step_calls,
    _finding_request_identity,
    _materialized_path_matches,
    _normalize_summary_path,
    _validate_api_path,
)
from .private_pilot_command_center_helpers import (  # noqa: F401
    _annotate_ui_risk_item,
    _build_internal_clue_contract,
    _collect_track_counts,
    _command_center_priority_label,
    _command_center_priority_score,
    _commercial_assets_signal,
    _defect_intake_fields,
    _defect_intake_stats,
    _is_ui_risk_item,
    _normalize_commercial_assets,
    _normalize_execution_evidence_summary,
    _rebuild_customer_display_contract,
    _select_commercial_assets,
    _ui_verification_stats,
)
from .private_pilot_command_center_builder import CommandCenterBuilderMixin
from .private_pilot_credentials_handlers import CredentialsHandlerMixin
from .private_pilot_http_routing import HttpRoutingMixin
from .private_pilot_ingest_handlers import IngestHandlersMixin
from .private_pilot_scan_handlers import ScanHandlersMixin
from .private_pilot_report_loading import ReportLoadingMixin
from .private_pilot_auth_scope import AuthScopeMixin
from .private_pilot_llm_health import LlmHealthMixin
from .private_pilot_continuous_handlers import ContinuousHandlersMixin
from .private_pilot_page_renders import PageRenderMixin
from .private_pilot_finding_utils import FindingUtilsMixin
from .private_pilot_ops_handlers import OpsHandlersMixin
from .private_pilot_continuous import (  # noqa: F401
    _CONTINUOUS_STATE_FILE,
    _continuous_scan_loop,
    _continuous_state_path,
    _continuous_threads,
    _get_continuous_state,
    _mark_continuous_converged,
    _mark_continuous_max_rounds,
    _record_continuous_failure,
    _update_continuous_state,
)
from .private_pilot_command_center_envelope import (  # noqa: F401
    clear_envelope_post_hooks,
    list_envelope_post_hooks,
    normalize_command_center_envelope,
    register_envelope_post_hook,
)
from .private_pilot_scan_prep import (  # noqa: F401
    _frontend_entry_url_candidates,
    _has_campaign_id_mismatch,
    _http_url_text,
    _is_local_private_service,
    _issue_runtime_approval_for_result,
    _load_followup_ui_execution_requests,
    _load_followup_ui_test_data_requests,
    _maybe_issue_local_runtime_approval,
    _predicted_campaign_binding,
    _prepare_v12_scan_body,
    _read_project_prd_text,
    _resolve_followup_ui_test_data_browser_plan,
    _resolve_scan_runtime_defaults,
    _resolve_ui_base_url_from_profile,
    _run_ingest_auto_scan,
    _validate_scan_base_url,
)


CONFIG_MANAGER_ROLES = {"project_owner", "qa_lead", "security_owner", "testops_admin", "admin"}
KNOWLEDGE_MANAGER_ROLES = {"knowledge_admin", "project_owner", "qa_lead", "admin"}
SETTINGS_MANAGER_ROLES = {"project_owner", "security_owner", "testops_admin", "admin"}

_TENANT = _current_tenant()


def _normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Thin dispatcher — patches register post-hooks instead of replacing this."""
    return normalize_command_center_envelope(payload)


class PrivatePilotHandler(
    HttpRoutingMixin,
    CredentialsHandlerMixin,
    IngestHandlersMixin,
    ScanHandlersMixin,
    ContinuousHandlersMixin,
    OpsHandlersMixin,
    PageRenderMixin,
    FindingUtilsMixin,
    LlmHealthMixin,
    ReportLoadingMixin,
    CommandCenterBuilderMixin,
    AuthScopeMixin,
    BaseHTTPRequestHandler,
):
    server_version = "QualiBugPrivatePilot/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return


def run_private_pilot_service(root: Path | None = None, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    root = root or _root()
    host = host or os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"} and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1":
        raise ValueError("Public binding is disabled by default. Set QUALIBUG_ALLOW_PUBLIC_BIND=1 only behind a trusted reverse proxy.")
    selected_port = int(os.environ.get("QUALIBUG_PORT", "8088")) if port is None else int(port)
    server = ThreadingHTTPServer((host, selected_port), PrivatePilotHandler)
    server.qualibug_private_root = root
    _dbg_report(
        hypothesis_id="A",
        msg="[DEBUG] private-pilot service bound",
        data={
            "pid": os.getpid(),
            "root": str(root),
            "host": host,
            "port": selected_port,
        },
    )
    return server


if __name__ == "__main__":
    raise SystemExit(
        "Unsupported launch path. Use qualibug-server "
        "(ai_test_asset_center.private_pilot_entrypoint:run_server)."
    )
