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


def build_enterprise_understanding_model(asset: dict[str, Any]) -> dict[str, Any]:
    resolution = resolve_enterprise_identities(asset)
    projected_asset = project_asset_for_legacy_builder(asset, resolution)
    model = _legacy.build_enterprise_understanding_model(projected_asset)
    return apply_identity_resolution_to_model(model, resolution)


__all__ = ["build_enterprise_understanding_model"]
