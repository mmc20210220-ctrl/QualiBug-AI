"""Enterprise-understanding builder with one identity authority.

The mature semantic projection remains in the historical sibling ``builder.py``.
This package shadows that module intentionally: identity is resolved first by
``identity_resolution``; the historical builder then receives a compatibility
projection with no alias authority and continues to build operations, lifecycles,
relations and processes.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from ..identity_resolution import (
    apply_identity_resolution_to_model,
    project_asset_for_legacy_builder,
    resolve_enterprise_identities,
)
from ..schema import as_list, stable_id, text

_PACKAGE = __package__.rsplit(".builder", 1)[0]
_LEGACY_NAME = f"{_PACKAGE}._semantic_projection_builder_v1"
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "builder.py"
_spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - import contract failure
    raise ImportError(f"cannot load semantic projection builder: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules.setdefault(_LEGACY_NAME, _legacy)
_spec.loader.exec_module(_legacy)

# Preserve private/public helpers used by existing tests and modules. Identity
# authority is overridden below; all other semantic projection code is reused.
for _name, _value in vars(_legacy).items():
    if _name.startswith("__") or _name == "build_enterprise_understanding_model":
        continue
    globals().setdefault(_name, _value)


def _govern_identity_conflicts(resolution: dict[str, Any]) -> None:
    for conflict in as_list(resolution.get("conflicts")):
        if not isinstance(conflict, dict):
            continue
        kind = text(conflict.get("kind")) or "ENTERPRISE_IDENTITY_CONFLICT"
        conflict.setdefault(
            "conflict_id",
            stable_id(
                "enterprise_identity_conflict",
                kind,
                conflict.get("alias") or conflict.get("label") or conflict.get("labels"),
                conflict.get("candidate_entity_ids"),
            ),
        )
        conflict.setdefault("reason_code", kind)
        conflict.setdefault("blocks_formal_understanding", True)


def build_enterprise_understanding_model(asset: dict[str, Any]) -> dict[str, Any]:
    resolution = resolve_enterprise_identities(asset)
    _govern_identity_conflicts(resolution)
    projected_asset = project_asset_for_legacy_builder(asset, resolution)
    model = _legacy.build_enterprise_understanding_model(projected_asset)
    return apply_identity_resolution_to_model(model, resolution)


__all__ = ["build_enterprise_understanding_model"]
