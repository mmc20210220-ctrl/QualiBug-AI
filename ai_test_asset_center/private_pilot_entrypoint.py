from __future__ import annotations

"""Deployment entrypoint for the patched private pilot HTTP service.

This module is the canonical executable entrypoint for local and Docker private
pilot deployments. It composes runtime patches before delegating to the legacy
HTTP server.
"""

import os
import time
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
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
from ai_test_asset_center.private_pilot_server import (
    install_customer_delivery_gate_patch,
    restore_customer_delivery_gate_patch,
)
from ai_test_asset_center.private_pilot_system_behavior_space_patch import (
    install_system_behavior_space_patch,
    restore_system_behavior_space_patch,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_entrypoint"
_RUNTIME_PATCHES_INSTALLED = False
_RUNTIME_PATCH_BASELINE_CALLABLES: dict[str, Any] = {}
_RUNTIME_PATCH_ACTIVE_CALLABLES: dict[str, Any] = {}


def _critical_runtime_callables() -> dict[str, Any]:
    from ai_test_asset_center import __main__ as scanner
    from ai_test_asset_center import regression_runner
    from ai_test_asset_center import regression_suite_builder
    from ai_test_asset_center import v12_pipeline

    return {
        "scan": scanner.scan,
        "command_center_normalizer": _service._normalize_command_center_envelope,
        "run_v12_pipeline": v12_pipeline.run_v12_pipeline,
        "regression_judge": regression_runner._judge_probe,
        "regression_loader": (
            regression_suite_builder._load_confirmed_findings_regression_probes
        ),
    }


def _callable_identity(value: Any) -> str:
    return (
        f"{getattr(value, '__module__', type(value).__module__)}:"
        f"{getattr(value, '__qualname__', getattr(value, '__name__', type(value).__name__))}"
    )


def _runtime_patch_markers() -> dict[str, bool]:
    from ai_test_asset_center import __main__ as scanner
    from ai_test_asset_center import business_state_graph
    from ai_test_asset_center import display_ready_formatter
    from ai_test_asset_center import regression_runner
    from ai_test_asset_center import regression_suite_builder
    from ai_test_asset_center import v12_pipeline

    return {
        "customer_delivery_gate": bool(
            getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False)
        ),
        "scan_campaign_context": bool(
            getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False)
        ),
        "credential_safety": bool(
            getattr(_service, "_ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS", None)
            and getattr(
                _service,
                "_ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS",
                None,
            )
        ),
        "regression_oracle": bool(
            getattr(
                regression_runner,
                "_ORIGINAL_REGRESSION_ORACLE_JUDGE",
                None,
            )
        ),
        "regression_suite_refresh": bool(
            getattr(scanner, "_ORIGINAL_REGRESSION_SUITE_REFRESH_SCAN", None)
        ),
        "system_behavior_space": bool(
            getattr(
                business_state_graph.BusinessStateGraphBuilder,
                "_ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE",
                None,
            )
            and getattr(
                regression_runner,
                "_ORIGINAL_JUDGE_PROBE_SYSTEM_BEHAVIOR",
                None,
            )
        ),
        "coverage_matrix": bool(
            getattr(_service, "_ORIGINAL_COVERAGE_MATRIX_NORMALIZER", None)
        ),
        "regression_run_visibility": bool(
            getattr(
                _service,
                "_ORIGINAL_REGRESSION_RUN_VISIBILITY_NORMALIZER",
                None,
            )
        ),
        "display_no_fix_advice": bool(
            getattr(
                display_ready_formatter,
                "_ORIGINAL_FORMAT_SINGLE_FINDING_NO_FIX",
                None,
            )
        ),
        "command_center_no_fix_advice": bool(
            getattr(_service, "_ORIGINAL_NO_FIX_ADVICE_NORMALIZER", None)
        ),
        "coverage_steering": bool(
            getattr(v12_pipeline, "_ORIGINAL_COVERAGE_STEERING_RUN", None)
        ),
        "browser_ui_smoke": bool(
            getattr(_service, "_BROWSER_UI_SMOKE_PATCHED", False)
        ),
        "customer_report": bool(
            getattr(_service, "_CUSTOMER_REPORT_PATCHED", False)
        ),
        "deployment_contract": bool(
            getattr(_service, "_DEPLOYMENT_CONTRACT_PATCHED", False)
        ),
        "regression_loader": bool(
            getattr(
                regression_suite_builder,
                "_ORIGINAL_REGRESSION_ORACLE_CONFIRMED_LOADER",
                None,
            )
        ),
    }


def runtime_patch_chain_status() -> dict[str, Any]:
    current = _critical_runtime_callables()
    expected = dict(_RUNTIME_PATCH_ACTIVE_CALLABLES)
    drift = sorted(
        name
        for name, callable_value in expected.items()
        if current.get(name) is not callable_value
    )
    markers = _runtime_patch_markers()
    missing_markers = sorted(
        name for name, active_marker in markers.items() if not active_marker
    )
    active = bool(expected) and not drift
    return {
        "patched": bool(
            _RUNTIME_PATCHES_INSTALLED and active and not missing_markers
        ),
        "declared_installed": bool(_RUNTIME_PATCHES_INSTALLED),
        "callable_identity_verified": active,
        "drifted_callables": drift,
        "markers": markers,
        "missing_markers": missing_markers,
        "active_callables": {
            name: _callable_identity(value)
            for name, value in sorted(current.items())
        },
    }


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
    global _RUNTIME_PATCHES_INSTALLED
    global _RUNTIME_PATCH_ACTIVE_CALLABLES
    global _RUNTIME_PATCH_BASELINE_CALLABLES
    _restore_deployment_contract_patch()
    restore_customer_report_patch()
    restore_browser_ui_smoke_patch()
    restore_coverage_steering_patch()
    restore_no_fix_advice_patch()
    restore_display_ready_no_fix_advice_patch()
    restore_regression_run_visibility_patch()
    restore_coverage_matrix_patch()
    restore_system_behavior_space_patch()
    restore_regression_suite_refresh_patch()
    restore_regression_oracle_patch()
    restore_service_credentials_patch()
    restore_scan_campaign_context_patch()
    restore_customer_delivery_gate_patch()
    if _RUNTIME_PATCH_BASELINE_CALLABLES:
        restored = _critical_runtime_callables()
        drift = sorted(
            name
            for name, callable_value in _RUNTIME_PATCH_BASELINE_CALLABLES.items()
            if restored.get(name) is not callable_value
        )
        if drift:
            raise RuntimeError(
                "runtime_patch_restore_incomplete:" + ",".join(drift)
            )
    _RUNTIME_PATCHES_INSTALLED = False
    _RUNTIME_PATCH_ACTIVE_CALLABLES = {}
    _RUNTIME_PATCH_BASELINE_CALLABLES = {}


def install_runtime_patches() -> None:
    global _RUNTIME_PATCHES_INSTALLED
    global _RUNTIME_PATCH_ACTIVE_CALLABLES
    global _RUNTIME_PATCH_BASELINE_CALLABLES
    if _RUNTIME_PATCHES_INSTALLED:
        status = runtime_patch_chain_status()
        if not status["patched"]:
            raise RuntimeError(
                "runtime_patch_chain_drift:"
                + ",".join(status["drifted_callables"])
            )
        return
    _RUNTIME_PATCH_BASELINE_CALLABLES = _critical_runtime_callables()
    try:
        install_customer_delivery_gate_patch()
        install_extracted_scan_campaign_context_patch()
        install_extracted_credential_safety_patch()
        install_regression_oracle_patch(patch_source=PATCH_SOURCE)
        install_regression_suite_refresh_patch(patch_source=PATCH_SOURCE)
        install_system_behavior_runtime_patch_chain()
        install_coverage_matrix_patch(
            patch_source=PATCH_SOURCE,
            root=_service._root(),
        )
        install_regression_run_visibility_patch(
            patch_source=PATCH_SOURCE,
            root=_service._root(),
        )
        install_display_ready_no_fix_advice_patch(patch_source=PATCH_SOURCE)
        install_no_fix_advice_patch(patch_source=PATCH_SOURCE)
        install_coverage_steering_patch(patch_source=PATCH_SOURCE)
        install_browser_ui_smoke_patch()
        install_customer_report_patch()
        install_deployment_contract_patch()
        missing_markers = sorted(
            name
            for name, active_marker in _runtime_patch_markers().items()
            if not active_marker
        )
        if missing_markers:
            raise RuntimeError(
                "runtime_patch_install_incomplete:" + ",".join(missing_markers)
            )
        _RUNTIME_PATCH_ACTIVE_CALLABLES = _critical_runtime_callables()
        _RUNTIME_PATCHES_INSTALLED = True
    except Exception as install_error:
        try:
            restore_deployment_contract_patch()
        except Exception as restore_error:
            raise RuntimeError(
                "runtime_patch_install_and_restore_failed:"
                f"install={type(install_error).__name__}:{install_error};"
                f"restore={type(restore_error).__name__}:{restore_error}"
            ) from install_error
        raise


def run_server() -> None:
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
    install_runtime_patches()
    server = _service.run_private_pilot_service()
    try:
        server.serve_forever()
    finally:
        # #region debug-point B:entrypoint-run-server-finally
        _service._dbg_report(
            hypothesis_id="B",
            msg="[DEBUG] private-pilot entrypoint stopping server",
            data={
                "pid": os.getpid(),
                "elapsed_ms": int((time.perf_counter() - _started) * 1000),
            },
        )
        # #endregion
        server.server_close()


if __name__ == "__main__":
    run_server()
