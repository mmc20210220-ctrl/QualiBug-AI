"""Schema and shape authority for the reference-only business world model."""
from __future__ import annotations

from typing import Any

from .schema import text

BUSINESS_WORLD_MODEL_SCHEMA = "qualibug.enterprise-business-world-model.v1"
BUSINESS_WORLD_MODEL_GATE_SCHEMA = "qualibug.enterprise-business-world-model-gate.v1"


def empty_business_world_model() -> dict[str, Any]:
    return {
        "schema": BUSINESS_WORLD_MODEL_SCHEMA,
        "object_nodes": [],
        "behavior_nodes": [],
        "edges": [],
        "identity_hypotheses": [],
        "evidence_registry": [],
        "gate": {
            "schema": BUSINESS_WORLD_MODEL_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "world_model_ready": False,
            "metrics": {},
        },
    }


def validate_business_world_model_shape(world: Any) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if not isinstance(world, dict):
        return [{"code": "BUSINESS_WORLD_MODEL_OBJECT_INVALID"}]
    if text(world.get("schema")) != BUSINESS_WORLD_MODEL_SCHEMA:
        violations.append(
            {
                "code": "BUSINESS_WORLD_MODEL_SCHEMA_INVALID",
                "value": world.get("schema"),
            }
        )
    for key in (
        "object_nodes",
        "behavior_nodes",
        "edges",
        "identity_hypotheses",
        "evidence_registry",
    ):
        if not isinstance(world.get(key), list):
            violations.append(
                {
                    "code": "BUSINESS_WORLD_MODEL_COLLECTION_INVALID",
                    "field": key,
                }
            )
    gate = world.get("gate")
    if not isinstance(gate, dict):
        violations.append({"code": "BUSINESS_WORLD_MODEL_GATE_INVALID"})
    elif gate and text(gate.get("schema")) != BUSINESS_WORLD_MODEL_GATE_SCHEMA:
        violations.append(
            {
                "code": "BUSINESS_WORLD_MODEL_GATE_SCHEMA_INVALID",
                "value": gate.get("schema"),
            }
        )
    return violations


__all__ = [
    "BUSINESS_WORLD_MODEL_SCHEMA",
    "BUSINESS_WORLD_MODEL_GATE_SCHEMA",
    "empty_business_world_model",
    "validate_business_world_model_shape",
]
