"""First-class post-processing hooks for ``__main__.scan``.

Repair/refresh installers register here instead of replacing ``scan``.
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

ScanPostHook = Callable[..., dict[str, Any]]

_LOGGER = logging.getLogger(__name__)
_SCAN_POST_HOOKS: dict[str, ScanPostHook | None] = {}
_BUILTIN_HOOK_INSTALLERS: tuple[tuple[str, str], ...] = (
    (
        "ai_test_asset_center.job_formal_planning_proof",
        "install_job_formal_planning_proof",
    ),
    (
        "ai_test_asset_center.validation_summary",
        "install_validation_summary",
    ),
    (
        "ai_test_asset_center.execution_evidence_report",
        "install_execution_evidence_report",
    ),
    (
        "ai_test_asset_center.performance_baseline",
        "install_performance_baseline",
    ),
    (
        "ai_test_asset_center.discovery_stability_loss_projection",
        "install_formal_stability_loss",
    ),
    (
        "ai_test_asset_center.behavior_semantic_mapper",
        "install_behavior_semantics",
    ),
    (
        "ai_test_asset_center.bug_risk_scoring",
        "install_bug_risk",
    ),
    (
        "ai_test_asset_center.behavior_registry",
        "install_behavior_registry",
    ),
    (
        "ai_test_asset_center.run_manifest_hook",
        "install_run_manifest_hook",
    ),
    (
        "ai_test_asset_center.scan_stage_finalization_hook",
        "install_scan_stage_finalization_hook",
    ),
)


def register_scan_post_hook(name: str, hook: ScanPostHook | None) -> None:
    key = str(name or "").strip()
    if not key:
        raise ValueError("scan post-hook name is required")
    if hook is None:
        _SCAN_POST_HOOKS.pop(key, None)
        return
    _SCAN_POST_HOOKS[key] = hook


def clear_scan_post_hooks() -> None:
    _SCAN_POST_HOOKS.clear()


def list_scan_post_hooks() -> list[str]:
    return [name for name, hook in _SCAN_POST_HOOKS.items() if callable(hook)]


def _install_builtin_scan_post_hooks() -> None:
    """Install product-owned projections idempotently for every public scan entry.

    Tests and hot-reload paths may clear the registry. Calling the installer on each
    scan is cheap and restores the same named hook without stacking wrappers.
    Import or installer failures remain non-blocking so a projection cannot hide the
    source scan result, but they must be observable: an unavailable projection is a
    real breadth loss and must never look identical to a successfully installed one.
    """
    for module_name, installer_name in _BUILTIN_HOOK_INSTALLERS:
        try:
            module = importlib.import_module(module_name)
            installer = getattr(module, installer_name, None)
            if callable(installer):
                installer()
            else:
                _LOGGER.warning(
                    "scan_post_hook_installer_missing module=%s installer=%s",
                    module_name,
                    installer_name,
                )
        except Exception as exc:
            _LOGGER.warning(
                "scan_post_hook_install_failed module=%s installer=%s error_type=%s error=%s",
                module_name,
                installer_name,
                type(exc).__name__,
                str(exc)[:300],
                exc_info=True,
            )


def apply_scan_post_hooks(
    result: Any,
    *,
    project: str,
    root: Path,
) -> Any:
    if not isinstance(result, dict):
        return result
    _install_builtin_scan_post_hooks()
    payload = result
    # P1 性能打点：逐钩子计时入 scan_phase_timings，收尾热点分布可追溯。
    import time as _time

    phase_timings = dict(payload.get("scan_phase_timings") or {})
    hook_timings: dict[str, float] = {}
    for name, hook in list(_SCAN_POST_HOOKS.items()):
        if not callable(hook):
            continue
        _hook_start = _time.perf_counter()
        try:
            next_payload = hook(payload, project=project, root=root)
        except Exception:
            # A post-hook must never mask the original scan result.
            _hook_ms = round(_time.perf_counter() - _hook_start, 3)
            hook_timings[name] = _hook_ms
            # 挂住/慢钩子必须仅凭日志归因（[wrapup-trace] 分段账本）。
            _LOGGER.warning(
                "[wrapup-trace] post_hook=%s ms=%s status=exception",
                name,
                _hook_ms,
                exc_info=True,
            )
            continue
        _hook_ms = round(_time.perf_counter() - _hook_start, 3)
        hook_timings[name] = _hook_ms
        _LOGGER.warning(
            "[wrapup-trace] post_hook=%s ms=%s",
            name,
            _hook_ms,
        )
        if isinstance(next_payload, dict):
            payload = next_payload
    try:
        existing = payload.get("scan_phase_timings")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged["post_hooks"] = hook_timings
        payload["scan_phase_timings"] = merged
    except Exception as exc:
        _LOGGER.debug(
            "scan_post_hook_timing_projection_failed error_type=%s error=%s",
            type(exc).__name__,
            str(exc)[:200],
            exc_info=True,
        )
    return payload
