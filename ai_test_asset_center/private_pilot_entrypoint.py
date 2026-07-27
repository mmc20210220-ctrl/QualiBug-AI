from __future__ import annotations

"""Deployment entrypoint for the patched private pilot HTTP service.

This module is the canonical executable entrypoint for local and Docker private
pilot deployments. It composes runtime patches before delegating to the legacy
HTTP server.
"""

import os
import platform
import shutil
import signal
import socket
import sys
import time
import traceback
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.product_logging import setup_product_logging, get_logger, audit_log
from ai_test_asset_center.error_codes import ErrorCode
from ai_test_asset_center import private_pilot_system_behavior_space_patch as _system_behavior_patch
from ai_test_asset_center.display_ready_no_fix_advice_patch import (
    install_display_ready_no_fix_advice_patch,
    restore_display_ready_no_fix_advice_patch,
)
from ai_test_asset_center.private_pilot_browser_bridge import (
    install_browser_ui_smoke_patch as _install_browser_ui_smoke_patch,
    restore_browser_ui_smoke_patch,
)
from ai_test_asset_center.private_pilot_coverage_matrix_patch import (
    install_coverage_matrix_patch,
    restore_coverage_matrix_patch,
)
from ai_test_asset_center.private_pilot_coverage_steering_patch import (
    install_coverage_steering_patch,
    restore_coverage_steering_patch,
)
from ai_test_asset_center.private_pilot_credentials_patch import (
    install_service_credentials_patch,
    restore_service_credentials_patch,
)
from ai_test_asset_center.private_pilot_customer_report_patch import (
    install_customer_report_patch as _install_customer_report_patch,
    restore_customer_report_patch,
)
from ai_test_asset_center.private_pilot_deployment_patch import (
    health_payload as _health_payload_impl,
    install_deployment_contract_patch as _install_deployment_contract_patch,
    restore_deployment_contract_patch as _restore_deployment_contract_patch,
)
from ai_test_asset_center.private_pilot_no_fix_advice_patch import (
    install_no_fix_advice_patch,
    restore_no_fix_advice_patch,
)
from ai_test_asset_center.persistence_assertions import install_persistence_surface
from ai_test_asset_center.private_pilot_regression_oracle_patch import (
    install_regression_oracle_patch,
    restore_regression_oracle_patch,
)
from ai_test_asset_center.private_pilot_regression_run_visibility_patch import (
    install_regression_run_visibility_patch,
    restore_regression_run_visibility_patch,
)
from ai_test_asset_center.private_pilot_regression_suite_refresh_patch import (
    install_regression_suite_refresh_patch,
    restore_regression_suite_refresh_patch,
)
from ai_test_asset_center.private_pilot_scan_context_patch import (
    install_scan_campaign_context_patch,
    restore_scan_campaign_context_patch,
)
from ai_test_asset_center.private_pilot_scan_result_repair_patch import (
    install_scan_result_repair_patch,
    restore_scan_result_repair_patch,
)
from ai_test_asset_center.private_pilot_server import install_command_center_runtime_support
from ai_test_asset_center.private_pilot_system_behavior_space_patch import (
    install_system_behavior_space_patch,
    restore_system_behavior_space_patch,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_entrypoint"

_logger = get_logger(__name__)


def _health_payload(handler: Any) -> dict[str, Any]:
    return _health_payload_impl(handler, patch_source=PATCH_SOURCE, fallback_root=_service._root())


def install_browser_ui_smoke_patch() -> None:
    _install_browser_ui_smoke_patch(patch_source=PATCH_SOURCE)


def install_customer_report_patch() -> None:
    _install_customer_report_patch(patch_source=PATCH_SOURCE)


def install_deployment_contract_patch() -> None:
    _install_deployment_contract_patch(patch_source=PATCH_SOURCE, fallback_root=_service._root())


def install_extracted_scan_campaign_context_patch() -> None:
    """Move scan campaign-context handlers from the legacy wrapper to the extracted module.

    The legacy customer-delivery wrapper still wires the scan bridge because it
    also installs the delivery gate. We restore only that scan bridge, then
    install the extracted module so private-pilot deployments use the smaller
    installer without changing the P0 source/campaign semantics.
    """
    restore_scan_campaign_context_patch()
    install_scan_campaign_context_patch(patch_source=PATCH_SOURCE)


def install_extracted_credential_safety_patch() -> None:
    """Move credential handlers from the legacy wrapper to the extracted module.

    The legacy customer-delivery wrapper still installs a credential guard while
    it wires scan campaign context. We restore that handler pair, then install
    the extracted credential patch so production entrypoints use the smaller
    module without requiring a risky full rewrite of private_pilot_server.py.
    """
    restore_service_credentials_patch()
    install_service_credentials_patch(patch_source=PATCH_SOURCE)


def install_system_behavior_runtime_patch_chain() -> None:
    """Install the System Behavior Space chain idempotently for real entrypoints.

    ``install_system_behavior_space_patch`` owns the primary BusinessStateGraph
    wrapper.  In long-running or hot-reloaded private deployments it may return
    early when that primary wrapper is already installed.  The auxiliary wrappers
    are still safe and idempotent, so the entrypoint explicitly re-checks them to
    avoid a partially-installed chain.
    """
    install_system_behavior_space_patch(patch_source=PATCH_SOURCE)
    _system_behavior_patch._install_v12_behavior_space_context_patch()
    _system_behavior_patch._install_system_behavior_scenario_patch()
    _system_behavior_patch._install_system_behavior_oracle_patch()
    _system_behavior_patch._install_system_behavior_finding_patch()
    _system_behavior_patch._install_system_behavior_regression_patch()


def restore_deployment_contract_patch() -> None:
    _restore_deployment_contract_patch()
    restore_regression_run_visibility_patch()
    restore_regression_suite_refresh_patch()
    restore_regression_oracle_patch()
    restore_no_fix_advice_patch()
    restore_display_ready_no_fix_advice_patch()
    restore_system_behavior_space_patch()
    restore_coverage_steering_patch()
    restore_coverage_matrix_patch()
    restore_scan_result_repair_patch()
    restore_scan_campaign_context_patch()
    restore_browser_ui_smoke_patch()
    restore_customer_report_patch()
    restore_service_credentials_patch()


def install_runtime_patches() -> None:
    """Install all runtime patches with per-patch error isolation.

    If any single patch fails (e.g. missing module, import error), the error
    is logged and the server continues booting with the remaining patches.
    Previously a single patch failure would prevent the entire server from
    starting.  Patch failures are reported through the debug channel so
    operators can investigate.
    """
    patches: list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]] = [
        # (label, installer_callable, args, kwargs) — ordered as before
        ("command_center_runtime_support", install_command_center_runtime_support, (), {}),
        ("scan_campaign_context", install_extracted_scan_campaign_context_patch, (), {}),
        ("credential_safety", install_extracted_credential_safety_patch, (), {}),
        ("scan_result_repair", install_scan_result_repair_patch, (), {"patch_source": PATCH_SOURCE}),
        ("regression_oracle", install_regression_oracle_patch, (), {"patch_source": PATCH_SOURCE}),
        ("regression_suite_refresh", install_regression_suite_refresh_patch, (), {"patch_source": PATCH_SOURCE}),
        ("system_behavior_runtime_chain", install_system_behavior_runtime_patch_chain, (), {}),
        # The persistence surface is the first non-http_api observer: it closes the
        # observer -> assertion-DSL -> risk-family chain for database-level defect
        # classes (state enumeration, field bounds) that every other observer is
        # structurally blind to (see AGENTS.md "Enterprise Business Comprehension
        # Contract"). Registration is idempotent and side-effect free at import time
        # (see persistence_observer.py); it only opens a connection when an
        # experiment actually requires this surface at run time.
        ("persistence_surface", install_persistence_surface, (), {}),
        ("coverage_matrix", install_coverage_matrix_patch, (), {"patch_source": PATCH_SOURCE, "root": _service._root()}),
        ("regression_run_visibility", install_regression_run_visibility_patch, (), {"patch_source": PATCH_SOURCE, "root": _service._root()}),
        ("display_ready_no_fix_advice", install_display_ready_no_fix_advice_patch, (), {"patch_source": PATCH_SOURCE}),
        ("no_fix_advice", install_no_fix_advice_patch, (), {"patch_source": PATCH_SOURCE}),
        ("coverage_steering", install_coverage_steering_patch, (), {"patch_source": PATCH_SOURCE}),
        ("browser_ui_smoke", install_browser_ui_smoke_patch, (), {}),
        ("customer_report", install_customer_report_patch, (), {}),
        ("deployment_contract", install_deployment_contract_patch, (), {}),
    ]

    failed: list[str] = []
    for label, installer, args, kwargs in patches:
        try:
            installer(*args, **kwargs)
        except Exception:
            failed.append(label)
            _logger.error(
                f"Runtime patch [{label}] failed — continuing",
                exc_info=True,
                extra={"error_code": ErrorCode.IMPORT_FAILED.code, "context": {"patch": label}},
            )
            _service._dbg_report(
                hypothesis_id="PATCH_FAIL",
                msg=f"[DEBUG] runtime patch [{label}] failed — continuing",
                data={"patch": label, "traceback": traceback.format_exc()[-600:]},
            )

    if failed:
        _logger.warning(
            f"{len(failed)}/{len(patches)} runtime patches failed",
            extra={"context": {"failed_patches": failed}},
        )
        _service._dbg_report(
            hypothesis_id="PATCH_SUMMARY",
            msg="[DEBUG] some runtime patches failed",
            data={"failed": failed, "total": len(patches)},
        )
    else:
        _logger.info(f"All {len(patches)} runtime patches installed successfully")


# ---------------------------------------------------------------------------
# Phase 5: Startup preflight checks
# ---------------------------------------------------------------------------


def _run_preflight_checks() -> list[dict[str, Any]]:
    """Run startup self-checks. Returns list of check results."""
    results: list[dict[str, Any]] = []

    def _check(name: str, ok: bool, detail: str, code: str = "", severity: str = "P2") -> None:
        results.append({"name": name, "ok": ok, "detail": detail, "code": code, "severity": severity})
        if not ok:
            _logger.warning(
                f"Preflight check failed: {name} - {detail}",
                extra={"error_code": code, "context": {"check": name, "severity": severity}},
            )

    # Python version
    py_ver = sys.version_info
    _check(
        "python_version",
        py_ver >= (3, 11),
        f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}",
        ErrorCode.PYTHON_VERSION.code,
        "P0",
    )

    # Port availability
    port = int(os.environ.get("QUALIBUG_PORT", "8088"))
    bind_host = os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1")
    port_ok = True
    port_detail = f"Port {port} available"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((bind_host if bind_host != "0.0.0.0" else "", port))
    except OSError as exc:
        port_ok = False
        port_detail = f"Port {port} unavailable: {exc}"
    _check("port_available", port_ok, port_detail, ErrorCode.PORT_IN_USE.code, "P0")

    # Disk space
    try:
        disk = shutil.disk_usage(os.getcwd())
        free_mb = disk.free / (1024 * 1024)
        _check(
            "disk_space",
            free_mb > 100,
            f"Free disk: {free_mb:.0f} MB",
            ErrorCode.DISK_SPACE_LOW.code,
            "P1",
        )
    except Exception:
        _check("disk_space", True, "Unable to check disk space (non-fatal)")

    # Required environment variables (informational, not blocking)
    jwt_secret = os.environ.get("QUALIBUG_JWT_SECRET", "").strip()
    _check(
        "jwt_secret",
        bool(jwt_secret),
        "QUALIBUG_JWT_SECRET configured" if jwt_secret else "QUALIBUG_JWT_SECRET not set (optional for local dev)",
        ErrorCode.ENV_MISSING.code,
        "P2",
    )

    # LLM endpoint reachability (TCP connect only, no real request)
    llm_base = os.environ.get("QUALIBUG_LLM_BASE_URL", os.environ.get("LLM_BASE_URL", "")).strip()
    if llm_base:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(llm_base)
            host = parsed.hostname or ""
            check_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if host:
                sock = socket.create_connection((host, check_port), timeout=5)
                sock.close()
                _check("llm_endpoint", True, f"LLM endpoint {host}:{check_port} reachable")
            else:
                _check("llm_endpoint", True, "LLM base URL configured (no host to probe)")
        except (OSError, ValueError) as exc:
            _check(
                "llm_endpoint",
                False,
                f"LLM endpoint unreachable: {exc}",
                ErrorCode.LLM_CONNECTION_REFUSED.code,
                "P0",
            )
    else:
        _check("llm_endpoint", True, "No LLM_BASE_URL configured (will use default)")

    return results


def run_server() -> None:
    # --- Product logging initialization (must be first) ---
    log_dir = setup_product_logging()

    # #region debug-point A:entrypoint-run-server
    _service._dbg_report(
        hypothesis_id="A",
        msg="[DEBUG] private-pilot entrypoint starting server",
        data={
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "port_env": os.environ.get("QUALIBUG_PORT", ""),
            "bind_host_env": os.environ.get("QUALIBUG_BIND_HOST", ""),
            "patch_source": PATCH_SOURCE,
        },
    )
    _started = time.perf_counter()
    # #endregion

    # --- Startup preflight checks ---
    preflight_results = _run_preflight_checks()
    failed_checks = [r for r in preflight_results if not r["ok"]]
    fatal_checks = [r for r in failed_checks if r["severity"] == "P0"]

    audit_log(
        "server_startup_preflight",
        context={
            "total_checks": len(preflight_results),
            "failed": len(failed_checks),
            "fatal": len(fatal_checks),
            "results": preflight_results,
            "python": sys.version.split()[0],
            "platform": platform.system(),
            "pid": os.getpid(),
            "log_dir": str(log_dir),
        },
    )

    if fatal_checks:
        for fc in fatal_checks:
            print(f"\n  [FATAL] {fc['name']}: {fc['detail']}")
            print(f"          错误码: {fc['code']}")
            print(f"          如问题持续，请运行: qualibug-doctor --export-bundle\n")
        _logger.error(
            "Server startup blocked by fatal preflight failures",
            extra={"error_code": ErrorCode.STARTUP_CHECK_FAILED.code, "context": {"fatal_checks": fatal_checks}},
        )
        raise SystemExit(1)

    if failed_checks:
        print(f"\n  [WARNING] 启动自检发现 {len(failed_checks)} 个问题（非致命，服务继续启动）:")
        for fc in failed_checks:
            print(f"    - {fc['name']}: {fc['detail']} ({fc['code']})")
        print(f"  如问题持续，请运行: qualibug-doctor --export-bundle\n")

    _logger.info(
        "Server starting",
        extra={"context": {
            "pid": os.getpid(),
            "port": os.environ.get("QUALIBUG_PORT", "8088"),
            "bind_host": os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1"),
            "python": sys.version.split()[0],
            "platform": platform.system(),
            "preflight_passed": len(preflight_results) - len(failed_checks),
            "preflight_failed": len(failed_checks),
        }},
    )

    install_runtime_patches()
    from ai_test_asset_center.credential_crypto import ensure_credential_key
    from ai_test_asset_center.policy_wiring import bind_product_installed_mainline_authority

    # ensure_credential_key() returns a str ("ok" / "plaintext_warning") and raises
    # when encryption is mandatory but unconfigured.  The previous isinstance(dict)
    # guard could never be true, so the plaintext case was silently swallowed here.
    _cred_key_status = ensure_credential_key()
    if _cred_key_status != "ok":
        _logger.warning(
            "Credential encryption key not configured -- credentials will be stored "
            "as plaintext.  Set QUALIBUG_CRED_ENC_KEY to enable at-rest encryption.",
            extra={
                "error_code": ErrorCode.KEY_MISSING.code,
                "context": {"credential_key_status": _cred_key_status},
            },
        )

    _mainline_bind = bind_product_installed_mainline_authority()
    _service._dbg_report(
        hypothesis_id="A",
        msg="[DEBUG] product mainline authority bound",
        data=_mainline_bind,
    )
    audit_log("mainline_authority_bound", context=_mainline_bind if isinstance(_mainline_bind, dict) else {})
    server = _service.run_private_pilot_service()

    _shutdown_requested = False

    def _on_shutdown(signum: int, frame: Any) -> None:
        nonlocal _shutdown_requested
        if _shutdown_requested:
            return
        _shutdown_requested = True
        _logger.info(
            "Shutdown signal received, draining connections",
            extra={"context": {"signal": signum, "elapsed_ms": int((time.perf_counter() - _started) * 1000)}},
        )
        audit_log(
            "server_shutdown_requested",
            context={"signal": signum, "elapsed_ms": int((time.perf_counter() - _started) * 1000)},
        )
        _service._dbg_report(
            hypothesis_id="B",
            msg="[DEBUG] received shutdown signal, draining connections",
            data={"signal": signum, "elapsed_ms": int((time.perf_counter() - _started) * 1000)},
        )
        # shutdown() is signal-safe: it sets an internal flag and does
        # not acquire locks; serve_forever() will exit on the next poll
        # cycle then finally: will call server_close() cleanly.
        server.shutdown()

    signal.signal(signal.SIGINT, _on_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_shutdown)

    try:
        server.serve_forever()
    except Exception as exc:
        _logger.critical(
            f"Server crashed: {exc}",
            exc_info=True,
            extra={"error_code": ErrorCode.UNHANDLED_EXCEPTION.code},
        )
        raise
    finally:
        # #region debug-point B:entrypoint-run-server-finally
        _elapsed = int((time.perf_counter() - _started) * 1000)
        _logger.info("Server stopped", extra={"context": {"pid": os.getpid(), "elapsed_ms": _elapsed}})
        audit_log("server_stopped", context={"pid": os.getpid(), "elapsed_ms": _elapsed})
        _service._dbg_report(
            hypothesis_id="B",
            msg="[DEBUG] private-pilot entrypoint stopping server",
            data={
                "pid": os.getpid(),
                "elapsed_ms": _elapsed,
            },
        )
        # #endregion
        server.server_close()


if __name__ == "__main__":
    run_server()
