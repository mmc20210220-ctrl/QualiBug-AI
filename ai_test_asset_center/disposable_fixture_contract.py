"""Compatibility facade for the legacy Disposable Fixture Contract.

The original discovery/build/receipt helpers remain available from
``disposable_fixture_contract_core`` for stored artifacts and isolated legacy
callers.  ExecutableExperiment compilation now declares
``fixture_data_authority=flow_data_requirement`` on its scoped Behavior IR; in
that scope candidate discovery is intentionally not run.  This prevents the
legacy primary-operation/first-candidate projection from becoming a second
compile authority.
"""
from __future__ import annotations

from typing import Any

from . import disposable_fixture_contract_core as _core


FLOW_DATA_AUTHORITY = "flow_data_requirement"


for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)


def discover_fixture_candidates(
    behavior_ir: dict[str, Any],
    *,
    entity_ids: "list[str] | None" = None,
) -> list[dict[str, Any]]:
    """Delegate only when the caller has not selected final-flow authority."""
    authority = str(
        getattr(behavior_ir, "fixture_data_authority", "") or ""
    ).strip()
    if authority == FLOW_DATA_AUTHORITY:
        return []
    return _core.discover_fixture_candidates(
        behavior_ir,
        entity_ids=entity_ids,
    )


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
