"""Public Behavior IR facade with fail-closed compensation derivation."""
from __future__ import annotations
from typing import Any

from . import behavior_ir_mainline_base as _base
from .compensation_derivation_authority import install_compensation_derivation_authority
from .database_body_reference_projection import project_database_body_reference_relations

install_compensation_derivation_authority(_base._core)

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_original_build_behavior_ir = _base.build_behavior_ir_from_knowledge_asset


def build_behavior_ir_from_knowledge_asset(
    asset: dict[str, Any] | None,
    *,
    project_id: str = "",
    source_snapshot_hash: str = "",
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    available_surfaces: dict[str, bool] | None = None,
) -> dict[str, Any]:
    model = _original_build_behavior_ir(
        asset,
        project_id=project_id,
        source_snapshot_hash=source_snapshot_hash,
        api_operations=api_operations,
        runtime_actors=runtime_actors,
        available_surfaces=available_surfaces,
    )
    model = project_database_body_reference_relations(model, asset)
    model["model_id"] = _base._core._content_addressed_id(model)
    return model

_base.build_behavior_ir_from_knowledge_asset = build_behavior_ir_from_knowledge_asset


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
