"""Public business-object recognition authority.

Recognition classifies source mentions by business role. Cross-source sameness,
aliases and stable IDs remain owned by ``identity_resolution``.
"""
from __future__ import annotations

from typing import Any

from ._object_candidate_governance import govern_object_candidates
from ._object_recognition_projection import (
    apply_recognition_to_model,
    project_asset_for_recognized_objects,
    publish_recognition_and_identity,
)
from ._object_source_preparation import (
    finalize_source_declared_recognition,
    prepare_source_declared_asset,
)


def recognize_business_objects(asset: dict[str, Any]) -> dict[str, Any]:
    prepared, authority = prepare_source_declared_asset(asset)
    return finalize_source_declared_recognition(
        govern_object_candidates(prepared), authority
    )


__all__ = [
    "apply_recognition_to_model",
    "project_asset_for_recognized_objects",
    "publish_recognition_and_identity",
    "recognize_business_objects",
]
