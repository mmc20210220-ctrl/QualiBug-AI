"""Keep visual baseline inventory summary counters on one stable denominator.

``include_revoked`` changes which rows are visible, but it must not silently
change the meaning of ``source_registered_count`` and ``approved_copy_count``.
Those primary counters now always mean active executable authority. Additional
``visible_*`` counters describe the currently returned history rows.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _visual_baselines as _registry

_INSTALL_MARKER = "_qualibug_visual_baseline_projection_guard_installed"
_ORIGINAL_LIST = "_qualibug_visual_baseline_list_before_projection_guard"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def install_visual_baseline_registry_projection_guard() -> None:
    if getattr(_registry, _INSTALL_MARKER, False):
        return
    original = getattr(
        _registry,
        _ORIGINAL_LIST,
        _registry.list_visual_baselines,
    )
    setattr(_registry, _ORIGINAL_LIST, original)

    def list_with_stable_summary(
        project_id: str,
        *,
        root: Path | None = None,
        include_revoked: bool = False,
    ) -> dict[str, Any]:
        result = original(
            project_id,
            root=root,
            include_revoked=include_revoked,
        )
        visible = [row for row in _list(result.get("baselines")) if isinstance(row, dict)]
        effective_root = Path(root or _registry.ROOT)
        project = _registry._safe_project_id(project_id)
        registry = _registry._load(project, effective_root)
        all_rows = [
            row
            for row in _list(registry.get("baselines"))
            if isinstance(row, dict)
        ]
        active = [row for row in all_rows if row.get("status") == "active"]
        result["summary"] = {
            "active_count": len(active),
            "revoked_count": sum(
                1 for row in all_rows if row.get("status") == "revoked"
            ),
            "source_registered_count": sum(
                1
                for row in active
                if row.get("authority") == "source_registered"
            ),
            "approved_copy_count": sum(
                1 for row in active if row.get("authority") == "approved_copy"
            ),
            "visible_count": len(visible),
            "visible_source_registered_count": sum(
                1
                for row in visible
                if row.get("authority") == "source_registered"
            ),
            "visible_approved_copy_count": sum(
                1
                for row in visible if row.get("authority") == "approved_copy"
            ),
        }
        result["summary_scope"] = {
            "source_registered_count": "active_authority",
            "approved_copy_count": "active_authority",
            "visible_counts": "returned_rows_after_include_revoked_filter",
        }
        return result

    _registry.list_visual_baselines = list_with_stable_summary
    setattr(_registry, _INSTALL_MARKER, True)


__all__ = ["install_visual_baseline_registry_projection_guard"]
