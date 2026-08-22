from __future__ import annotations

"""Canonical deployment entrypoint for the private-pilot HTTP service.

The service is either fully wired or it does not bind a port. Runtime component
installation, public authentication prerequisites and credential encryption are
startup invariants rather than best-effort warnings.
"""

import os
import platform
import shutil
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center import (
    private_pilot_system_behavior_space_patch as _system_behavior_patch,
)
from ai_test_asset_center.display_ready_no_fix_advice_patch import (
    install_display_ready_no_fix_advice_patch,
    restore_display_ready_no_fix_advice_patch,
)
from ai_test_asset_center.error_codes import ErrorCode
from ai_test_asset_center.persistence_assertions import install_persistence_surface
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
from ai_test_asset_center.private_pilot_server import (
    install_command_center_runtime_support,
)
from ai_test_asset_center.private_pilot_system_behavior_space_patch import (
    install_system_behavior_space_patch,
    restore_system_behavior_space_patch,
)
from ai_test_asset_center.product_logging import (
    audit_log,
    get_logger,
    setup_product_logging,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_entrypoint"
_logger = get_logger(__name__)


class RuntimeWiringError(RuntimeError):
    """The product mainline could not be wired completely."""


def _health_payload(handler: Any) -> dict[str, Any]:
    return _health_payload_impl(
        handler,
        patch_source=PATCH_SOURCE,
        fallback_root=_service._root(),
    )


def install_browser_ui_smoke_patch() -> None:
    _install_browser_ui_smoke_patch(patch_source=PATCH_SOURCE)


def install_customer_report_patch() -> None:
    _install_customer_report_patch(patch_source=PATCH_SOURCE)


def install_deployment_contract_patch() -> None:
    _install_deployment_contract_patch(
        patch_source=PATCH_SOURCE,
        fallback_root=_service._root(),
    )


def install_extracted_scan_campaign_context_patch() -> None:
    restore_scan_campaign_context_patch()
    install_scan_campaign_context_patch(patch_source=PATCH_SOURCE)


def install_extracted_credential_safety_patch() -> None:
    restore_service_credentials_patch()
    install_service_credentials_patch(patch_source=PATCH_SOURCE)


def install_system_behavior_runtime_patch_chain() -> None:
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


def install_runtime_components() -> list[str]:
    """Install every required runtime component or fail before socket bind."""

    components: list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]] = [
        (
            "command_center_runtime_support",
            install_command_center_runtime_support,
            (),
            {},
        ),
        (
            "scan_campaign_context",
            install_extracted_scan_campaign_context_patch,
            (),
            {},
        ),
        (
            "credential_safety",
            install_extracted_credential_safety_patch,
            (),
            {},
        ),
        (
            "scan_result_repair",
            install_scan_result_repair_patch,
            (),
            {"patch_source": PATCH_SOURCE},
        ),
        (
            "regression_oracle",
            install_regression_oracle_patch,
            (),
            {"patch_source": PATCH_SOURCE},
        ),
        (
            "regression_suite_refresh",
            install_regression_suite_refresh_patch,
            (),
            {"patch_source": PATCH_SOURCE},
        ),
        (
            "system_behavior_runtime_chain",
            install_system_behavior_runtime_patch_chain,
            (),
            {},
        ),
        ("persistence_surface", install_persistence_surface, (), {}),
        (
            "coverage_matrix",
            install_coverage_matrix_patch,
            (),
            {"patch_source": PATCH_SOURCE, "root": _service._root()},
        ),
        (
            "regression_run_visibility",
            install_regression_run_visibility_patch,
            (),
            {"patch_source": PATCH_SOURCE, "root": _service._root()},
        ),
        (
            "display_ready_no_fix_advice",
            install_display_ready_no_fix_advice_patch,
            (),
            {"patch_source": PATCH_SOURCE},
        ),
        (
            "no_fix_advice",
            install_no_fix_advice_patch,
            (),
            {"patch_source": PATCH_SOURCE},
        ),
        (
            "coverage_steering",
            install_coverage_steering_patch,
            (),
            {"patch_source": PATCH_SOURCE},
        ),
        ("browser_ui_smoke", install_browser_ui_smoke_patch, (), {}),
        ("customer_report", install_customer_report_patch, (), {}),
        ("deployment_contract", install_deployment_contract_patch, (), {}),
    ]
    installed: list[str] = []
    for label, installer, args, kwargs in components:
        try:
            installer(*args, **kwargs)
        except Exception as exc:
            _logger.critical(
                "Required runtime component failed: %s",
                label,
                exc_info=True,
                extra={
                    "error_code": ErrorCode.IMPORT_FAILED.code,
                    "context": {
                        "component": label,
                        "exception_type": type(exc).__name__,
                    },
                },
            )
            audit_log(
                "runtime_component_install_failed",
                context={
                    "component": label,
                    "exception_type": type(exc).__name__,
                },
            )
            raise RuntimeWiringError(
                f"required runtime component failed: {label}"
            ) from exc
        installed.append(label)
    _logger.info(
        "All required runtime components installed",
        extra={"context": {"components": installed}},
    )
    audit_log(
        "runtime_components_installed",
        context={"components": installed},
    )
    return installed


def install_runtime_patches() -> None:
    """Backward-compatible entrypoint with fail-closed semantics."""

    install_runtime_components()


def _run_preflight_checks() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def check(
        name: str,
        ok: bool,
        detail: str,
        code: str = "",
        severity: str = "P2",
    ) -> None:
        result = {
            "name": name,
            "ok": bool(ok),
            "detail": detail,
            "code": code,
            "severity": severity,
        }
        results.append(result)
        if not ok:
            _logger.warning(
                "Preflight check failed: %s - %s",
                name,
                detail,
                extra={
                    "error_code": code,
                    "context": {"check": name, "severity": severity},
                },
            )

    python_version = sys.version_info
    check(
        "python_version",
        python_version >= (3, 11),
        (
            f"Python {python_version.major}.{python_version.minor}."
            f"{python_version.micro}"
        ),
        ErrorCode.PYTHON_VERSION.code,
        "P0",
    )

    try:
        port = int(os.environ.get("QUALIBUG_PORT", "8088"))
    except ValueError:
        port = -1
    bind_host = os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1")
    port_ok = 0 < port <= 65535
    port_detail = f"Port {port} available"
    if port_ok:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((bind_host if bind_host != "0.0.0.0" else "", port))
        except OSError as exc:
            port_ok = False
            port_detail = f"Port {port} unavailable: {exc}"
    else:
        port_detail = "QUALIBUG_PORT must be an integer between 1 and 65535"
    check(
        "port_available",
        port_ok,
        port_detail,
        ErrorCode.PORT_IN_USE.code,
        "P0",
    )

    try:
        disk = shutil.disk_usage(_service._root())
        free_mb = disk.free / (1024 * 1024)
        check(
            "disk_space",
            free_mb > 100,
            f"Free disk: {free_mb:.0f} MB",
            ErrorCode.DISK_SPACE_LOW.code,
            "P1",
        )
    except Exception as exc:
        check(
            "disk_space",
            False,
            f"Unable to check disk space: {type(exc).__name__}",
            ErrorCode.DISK_SPACE_LOW.code,
            "P1",
        )

    public_bind = (
        bind_host in {"0.0.0.0", "::"}
        or os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1"
    )
    jwt_secret = os.environ.get("QUALIBUG_JWT_SECRET", "").strip()
    secret_ok = bool(jwt_secret) or not public_bind
    check(
        "jwt_secret",
        secret_ok,
        (
            "QUALIBUG_JWT_SECRET configured"
            if jwt_secret
            else "QUALIBUG_JWT_SECRET is mandatory for public binding"
            if public_bind
            else "JWT secret may be configured before using authenticated local routes"
        ),
        ErrorCode.ENV_MISSING.code,
        "P0" if public_bind else "P1",
    )

    if public_bind:
        check(
            "public_bind_opt_in",
            os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1",
            "Public binding requires QUALIBUG_ALLOW_PUBLIC_BIND=1",
            ErrorCode.ENV_MISSING.code,
            "P0",
        )
        check(
            "auth_bypass_disabled",
            os.environ.get("QUALIBUG_AUTH_BYPASS") != "1",
            "Authentication bypass is forbidden on public binding",
            ErrorCode.ENV_MISSING.code,
            "P0",
        )

    llm_base = os.environ.get(
        "QUALIBUG_LLM_BASE_URL",
        os.environ.get("LLM_BASE_URL", ""),
    ).strip()
    if llm_base:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(llm_base)
            host = parsed.hostname or ""
            check_port = parsed.port or (
                443 if parsed.scheme == "https" else 80
            )
            if not host:
                raise ValueError("LLM base URL has no host")
            with socket.create_connection((host, check_port), timeout=5):
                pass
            check(
                "llm_endpoint",
                True,
                f"LLM endpoint {host}:{check_port} reachable",
            )
        except (OSError, ValueError) as exc:
            check(
                "llm_endpoint",
                False,
                f"LLM endpoint unreachable: {exc}",
                ErrorCode.LLM_CONNECTION_REFUSED.code,
                "P1",
            )
    else:
        check(
            "llm_endpoint",
            True,
            "No explicit LLM endpoint configured",
        )
    return results


def _block_on_fatal_preflight(
    results: list[dict[str, Any]],
    *,
    log_dir: Path,
) -> None:
    failed = [row for row in results if not row["ok"]]
    fatal = [row for row in failed if row["severity"] == "P0"]
    audit_log(
        "server_startup_preflight",
        context={
            "total_checks": len(results),
            "failed": len(failed),
            "fatal": len(fatal),
            "results": results,
            "python": sys.version.split()[0],
            "platform": platform.system(),
            "pid": os.getpid(),
            "log_dir": str(log_dir),
        },
    )
    if fatal:
        _logger.critical(
            "Server startup blocked by fatal preflight failures",
            extra={
                "error_code": ErrorCode.STARTUP_CHECK_FAILED.code,
                "context": {"fatal_checks": fatal},
            },
        )
        raise SystemExit(1)
    if failed:
        _logger.warning(
            "Non-fatal startup checks failed",
            extra={"context": {"failed_checks": failed}},
        )


def run_server() -> None:
    log_dir = setup_product_logging()
    # Startup-time .env loading: default order is <root>/.env then .env.local
    # (existing process environment always wins). Operators may point
    # QUALIBUG_ENV_FILE at an explicit file to load exactly that one.
    # Previously dotenv loading only happened lazily inside the LLM client,
    # so documented settings (QUALIBUG_PORT, JWT secret, provider keys) written
    # to .env.local never applied on the bare-metal launch path.
    from aitestops.env_loader import load_dotenv

    env_file = os.environ.get("QUALIBUG_ENV_FILE", "").strip()
    loaded_env = load_dotenv(Path(env_file) if env_file else None)
    if loaded_env:
        _logger.info(
            "Loaded environment file(s) at startup",
            extra={"context": {"env_file": env_file or "<root>/.env + .env.local", "keys": sorted(loaded_env)}},
        )
    started = time.perf_counter()
    _service._dbg_report(
        hypothesis_id="STARTUP",
        msg="[DEBUG] private-pilot entrypoint starting",
        data={
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "port_env": os.environ.get("QUALIBUG_PORT", ""),
            "bind_host_env": os.environ.get("QUALIBUG_BIND_HOST", ""),
            "component_source": PATCH_SOURCE,
        },
    )
    _block_on_fatal_preflight(
        _run_preflight_checks(),
        log_dir=Path(log_dir),
    )

    install_runtime_components()

    from ai_test_asset_center.credential_crypto import ensure_credential_key
    from ai_test_asset_center.policy_wiring import (
        bind_product_installed_mainline_authority,
    )
    from ai_test_asset_center.private_pilot_credentials_patch import (
        ensure_local_credential_encryption_key,
    )

    private_root = _service._root()
    credential_key_source = ensure_local_credential_encryption_key(private_root)
    credential_key_status = ensure_credential_key()
    if credential_key_status != "ok":
        _logger.warning(
            "Credential encryption is not mandatory in this local deployment",
            extra={
                "error_code": ErrorCode.KEY_MISSING.code,
                "context": {
                    "credential_key_status": credential_key_status,
                    "credential_key_source": credential_key_source,
                },
            },
        )

    mainline_binding = bind_product_installed_mainline_authority()
    if not isinstance(mainline_binding, dict) or mainline_binding.get("ok") is False:
        raise RuntimeWiringError("product mainline authority binding failed")
    _service._dbg_report(
        hypothesis_id="STARTUP",
        msg="[DEBUG] product mainline authority bound",
        data=mainline_binding,
    )
    audit_log("mainline_authority_bound", context=mainline_binding)
    server = _service.run_private_pilot_service(root=private_root)
    shutdown_requested = False

    def on_shutdown(signum: int, frame: Any) -> None:
        del frame
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _logger.info(
            "Shutdown signal received",
            extra={"context": {"signal": signum, "elapsed_ms": elapsed_ms}},
        )
        audit_log(
            "server_shutdown_requested",
            context={"signal": signum, "elapsed_ms": elapsed_ms},
        )
        server.shutdown()

    signal.signal(signal.SIGINT, on_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_shutdown)
    try:
        server.serve_forever()
    except Exception as exc:
        _logger.critical(
            "Server crashed: %s",
            exc,
            exc_info=True,
            extra={"error_code": ErrorCode.UNHANDLED_EXCEPTION.code},
        )
        raise
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        server.server_close()
        _logger.info(
            "Server stopped",
            extra={"context": {"pid": os.getpid(), "elapsed_ms": elapsed_ms}},
        )
        audit_log(
            "server_stopped",
            context={"pid": os.getpid(), "elapsed_ms": elapsed_ms},
        )


if __name__ == "__main__":
    run_server()
