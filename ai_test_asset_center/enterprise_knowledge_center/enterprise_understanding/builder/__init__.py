"""Enterprise-understanding builder with one identity authority.

The mature semantic projection remains in the historical sibling ``builder.py``.
Business-object type, identity evidence, stable registry drift, technical bindings,
authority receipts and optional external measurement are closed before projection.
"""
from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..business_object_recognition import (
    apply_recognition_to_model,
    project_asset_for_recognized_objects,
    publish_recognition_and_identity,
    recognize_business_objects,
)
from ..identity_authority_projection import project_identity_authority_receipt
from ..identity_benchmark import project_identity_benchmark
from ..identity_evidence_policy import apply_identity_evidence_policy
from ..identity_registry_governance import govern_identity_registry
from ..identity_resolution import (
    apply_identity_resolution_to_model,
    project_asset_for_legacy_builder,
    resolve_enterprise_identities,
)
from ..identity_technical_projection import augment_technical_identity_projection
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

# Preserve private/public helpers used by existing tests and modules. Object-type
# and identity authority are overridden below; semantic projection is reused.
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
                conflict.get("candidate_entity_ids") or conflict.get("prior_entity_id"),
            ),
        )
        conflict.setdefault("reason_code", kind)
        conflict.setdefault("blocks_formal_understanding", True)


def build_enterprise_understanding_model(asset: dict[str, Any]) -> dict[str, Any]:
    prior_registry = deepcopy(asset.get("enterprise_identity_registry") or {})
    apply_identity_evidence_policy(asset)
    recognition = recognize_business_objects(asset)
    recognized_asset = project_asset_for_recognized_objects(asset, recognition)
    resolution = resolve_enterprise_identities(recognized_asset)
    resolution = govern_identity_registry(prior_registry, resolution, asset=recognized_asset)
    resolution = augment_technical_identity_projection(recognized_asset, resolution)
    resolution = project_identity_authority_receipt(recognized_asset, resolution)
    resolution = project_identity_benchmark(recognized_asset, resolution)
    _govern_identity_conflicts(resolution)
    publish_recognition_and_identity(asset, recognized_asset, resolution)
    projected_asset = project_asset_for_legacy_builder(recognized_asset, resolution)
    model = _legacy.build_enterprise_understanding_model(projected_asset)
    model = apply_identity_resolution_to_model(model, resolution)
    return apply_recognition_to_model(model, recognition)


__all__ = ["build_enterprise_understanding_model"]
