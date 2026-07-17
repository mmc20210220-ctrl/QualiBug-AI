"""First-class post-processing hooks for ``__main__.scan``.

Repair/refresh installers register here instead of replacing ``scan``.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

ScanPostHook = Callable[..., dict[str, Any]]

_SCAN_POST_HOOKS: dict[str, ScanPostHook | None] = {}


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


def apply_scan_post_hooks(
    result: Any,
    *,
    project: str,
    root: Path,
) -> Any:
    if not isinstance(result, dict):
        return result
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
