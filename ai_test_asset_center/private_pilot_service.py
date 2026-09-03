from __future__ import annotations

"""Private-network HTTP composition root for the QualiBug pilot runtime.

Authentication and authorization are handled by the focused mixins. This module
composes the canonical handler and owns the hardened HTTP server lifecycle.
"""

import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .private_pilot_request_limits import (
    MAX_REQUEST_BODY_BYTES,
    content_length,
)

_HTTP_TIMEOUT = int(os.environ.get("QUALIBUG_HTTP_TIMEOUT", "30"))
_REQUEST_QUEUE_SIZE = int(os.environ.get("QUALIBUG_REQUEST_QUEUE_SIZE", "128"))

from . import db_persistence as db_persist  # noqa: F401
from .connector_auto_sync import (
    ensure_connector_auto_sync_supervisor,
    stop_connector_auto_sync_supervisor,
)
from .customer_delivery_gate import (
    split_customer_delivery_tracks as _partition_delivery_tracks,
)
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
from . import jwt_auth  # noqa: F401
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
from .private_pilot_command_center_understanding import (
    UnderstandingCommandCenterProjectionMixin,
)
from .private_pilot_connector_handlers import KnowledgeConnectorHandlersMixin
from .private_pilot_connector_read_fast_path import ConnectorReadFastPathMixin
from .private_pilot_finding_collaboration_handlers import (
    FindingCollaborationHandlersMixin,
)
from .private_pilot_credentials_handlers import CredentialsHandlerMixin
from .private_pilot_product_catalog import ProductCatalogHttpMixin
from .private_pilot_http_routing import HttpRoutingMixin
from .private_pilot_identity_benchmark_handlers import IdentityBenchmarkHttpMixin
from .private_pilot_ingest_handlers import IngestHandlersMixin
from .private_pilot_understanding_preflight import (
    UnderstandingPreflightProjectionMixin,
)
from .private_pilot_scan_handlers import ScanHandlersMixin
from .private_pilot_historical_authorization_migration import (
    HistoricalAuthorizationReportMigrationMixin,
)
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

CONFIG_MANAGER_ROLES = {
    "project_owner",
    "qa_lead",
    "security_owner",
    "testops_admin",
    "admin",
}
KNOWLEDGE_MANAGER_ROLES = {
    "knowledge_admin",
    "project_owner",
    "qa_lead",
    "admin",
}
SETTINGS_MANAGER_ROLES = {
    "project_owner",
    "security_owner",
    "testops_admin",
    "admin",
}

_TENANT = _current_tenant()


def _normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_command_center_envelope(payload)


class PrivatePilotHandler(
    IdentityBenchmarkHttpMixin,
    ConnectorReadFastPathMixin,
    KnowledgeConnectorHandlersMixin,
    FindingCollaborationHandlersMixin,
    ProductCatalogHttpMixin,
    HttpRoutingMixin,
    CredentialsHandlerMixin,
    IngestHandlersMixin,
    UnderstandingPreflightProjectionMixin,
    ScanHandlersMixin,
    ContinuousHandlersMixin,
    OpsHandlersMixin,
    PageRenderMixin,
    FindingUtilsMixin,
    LlmHealthMixin,
    HistoricalAuthorizationReportMigrationMixin,
    ReportLoadingMixin,
    UnderstandingCommandCenterProjectionMixin,
    CommandCenterBuilderMixin,
    AuthScopeMixin,
    BaseHTTPRequestHandler,
):
    server_version = "QualiBugPrivatePilot/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _require_connector_manager(
        self,
        actor: dict[str, Any],
        action: str,
    ) -> bool:
        """Keep connector mutations on the same authority as knowledge sources."""
        return bool(
            self._require_role(
                actor,
                KNOWLEDGE_MANAGER_ROLES,
                action,
            )
        )

    def handle_one_request(self) -> None:
        """Parse one request, enforce the shared outer body bound, dispatch once."""
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.send_error(414)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            length = content_length(self.headers)
        except ValueError:
            self.send_error(400, "Bad Request: invalid Content-Length")
            return
        except Exception:
            self.close_connection = True
            return
        if length > MAX_REQUEST_BODY_BYTES:
            self.send_error(413, "Payload Too Large")
            return
        method_name = "do_" + self.command
        if not hasattr(self, method_name):
            self.send_error(501, "Unsupported method (%r)" % self.command)
            return
        getattr(self, method_name)()
        self.wfile.flush()


class QualiBugHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with bounded connections and safe shutdown."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
    ) -> None:
        self.request_queue_size = _REQUEST_QUEUE_SIZE
        self.timeout = _HTTP_TIMEOUT
        self._serve_thread_ident: int | None = None
        self._shutdown_dispatch_lock = threading.Lock()
        self._shutdown_dispatched = False
        self.qualibug_private_root: Path | None = None
        self.qualibug_connector_auto_sync: dict[str, Any] = {}
        super().__init__(server_address, handler_class)

    def server_bind(self) -> None:
        super().server_bind()
        if hasattr(self, "socket") and self.socket is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        if self.timeout and self.timeout > 0:
            connection.settimeout(self.timeout)
        return connection, address

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self._serve_thread_ident = threading.get_ident()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            self._serve_thread_ident = None

    def shutdown(self) -> None:
        """Stop serving without ever waiting on the serving thread itself."""
        if threading.get_ident() != self._serve_thread_ident:
            return super().shutdown()
        with self._shutdown_dispatch_lock:
            if self._shutdown_dispatched:
                return
            self._shutdown_dispatched = True
            shutdown_call = super().shutdown
            threading.Thread(
                target=shutdown_call,
                name="qualibug-http-shutdown",
                daemon=True,
            ).start()

    def server_close(self) -> None:
        """Stop background maintenance before releasing the listening socket."""

        root = self.qualibug_private_root
        if isinstance(root, Path):
            stop_connector_auto_sync_supervisor(root, join_timeout=5.0)
        super().server_close()


_global_server: QualiBugHTTPServer | None = None


def run_private_pilot_service(
    root: Path | None = None,
    host: str | None = None,
    port: int | None = None,
) -> QualiBugHTTPServer:
    global _global_server
    resolved_root = (root or _root()).resolve()
    resolved_host = host or os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1")
    if (
        resolved_host in {"0.0.0.0", "::"}
        and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
    ):
        raise ValueError(
            "Public binding is disabled by default. Set "
            "QUALIBUG_ALLOW_PUBLIC_BIND=1 only behind a trusted reverse proxy."
        )
    selected_port = (
        int(os.environ.get("QUALIBUG_PORT", "8088"))
        if port is None
        else int(port)
    )
    if not 0 < selected_port <= 65535:
        raise ValueError("QUALIBUG_PORT must be between 1 and 65535")
    server = QualiBugHTTPServer(
        (resolved_host, selected_port),
        PrivatePilotHandler,
    )
    server.qualibug_private_root = resolved_root
    try:
        server.qualibug_connector_auto_sync = (
            ensure_connector_auto_sync_supervisor(resolved_root)
        )
    except Exception:
        ThreadingHTTPServer.server_close(server)
        raise
    _global_server = server
    _dbg_report(
        hypothesis_id="A",
        msg="[DEBUG] private-pilot service bound",
        data={
            "pid": os.getpid(),
            "root": str(resolved_root),
            "host": resolved_host,
            "port": selected_port,
            "timeout": _HTTP_TIMEOUT,
            "request_queue_size": _REQUEST_QUEUE_SIZE,
            "max_request_body": MAX_REQUEST_BODY_BYTES,
            "connector_auto_sync_started_at_service_creation": True,
            "connector_auto_sync": server.qualibug_connector_auto_sync,
        },
    )
    return server


if __name__ == "__main__":
    raise SystemExit(
        "Unsupported launch path. Use qualibug-server "
        "(ai_test_asset_center.private_pilot_entrypoint:run_server)."
    )
