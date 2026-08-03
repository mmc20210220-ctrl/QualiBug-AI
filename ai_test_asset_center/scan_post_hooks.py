"""First-class post-processing hooks for ``__main__.scan``.

Repair/refresh installers register here instead of replacing ``scan``.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

ScanPostHook = Callable[..., dict[str, Any]]

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
    Import or installer failures are isolated so a projection cannot hide the source
    scan result.
    """
    for module_name, installer_name in _BUILTIN_HOOK_INSTALLERS:
        try:
            module = importlib.import_module(module_name)
            installer = getattr(module, installer_name, None)
            if callable(installer):
                installer()
        except Exception:
            continue


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
    for name, hook in list(_SCAN_POST_HOOKS.items()):
        if not callable(hook):
            continue
        try:
            next_payload = hook(payload, project=project, root=root)
        except Exception:
            # A post-hook must never mask the original scan result.
            continue
        if isinstance(next_payload, dict):
            payload = next_payload
    return payload
