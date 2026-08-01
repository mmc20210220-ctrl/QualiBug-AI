"""Canonical reference-only view over the governed enterprise understanding model.

The enterprise understanding model remains the semantic authority. This module does
not extract, infer, merge, or copy a second set of business facts. It publishes a
stable reference graph for downstream planning after identity closure.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._business_world_model_graph import (
    build_evidence_registry,
    build_identity_hypotheses,
    build_world_edges,
)
from ._business_world_model_integrity import validate_world_integrity
from ._business_world_model_nodes import build_behavior_nodes, build_object_nodes
from ._business_world_model_common import _rows
from .business_world_model_schema import (
    BUSINESS_WORLD_MODEL_GATE_SCHEMA,
    BUSINESS_WORLD_MODEL_SCHEMA,
)
from .schema import as_dict, stable_id, text


def build_business_world_model(model: dict[str, Any]) -> dict[str, Any]:
    """Build a reference-only world view from an already governed understanding model."""

    object_nodes = build_object_nodes(model)
    behavior_nodes = build_behavior_nodes(model)
    edges = build_world_edges(model)
    hypotheses = build_identity_hypotheses(model)
    evidence_registry = build_evidence_registry(model)
    world = {
        "schema": BUSINESS_WORLD_MODEL_SCHEMA,
        "world_model_id": stable_id(
            "business_world_model",
            model.get("model_id"),
            [row.get("node_id") for row in object_nodes],
            [row.get("node_id") for row in behavior_nodes],
            [row.get("edge_id") for row in edges],
            [row.get("hypothesis_id") for row in hypotheses],
        ),
        "source_model_id": text(model.get("model_id")),
        "object_nodes": object_nodes,
        "behavior_nodes": behavior_nodes,
        "edges": edges,
        "identity_hypotheses": hypotheses,
        "evidence_registry": evidence_registry,
        "state_contract": ["CONFIRMED", "SUSPECTED", "CONFLICTED", "UNKNOWN"],
        "semantic_authority": "ENTERPRISE_UNDERSTANDING_MODEL",
        "reference_only_projection": True,
        "semantic_payload_duplication_allowed": False,
        "candidate_object_nodes_allowed": False,
        "automatic_entity_union_allowed": False,
        "name_similarity_is_identity_authority": False,
    }
    violations = validate_world_integrity(world, model)
    upstream_gate = as_dict(model.get("gate"))
    upstream_allowed = bool(upstream_gate.get("entry_allowed"))
    status = "PASS"
    if violations:
        status = "BLOCKED_WORLD_MODEL_INTEGRITY"
    elif not upstream_allowed:
        status = "BLOCKED_UPSTREAM_UNDERSTANDING"
    world["gate"] = {
        "schema": BUSINESS_WORLD_MODEL_GATE_SCHEMA,
        "status": status,
        "entry_allowed": not violations and upstream_allowed,
        "world_model_ready": not violations,
        "downstream_candidate_generation_allowed": not violations and upstream_allowed,
        "automatic_entity_union_allowed": False,
        "integrity_violations": violations,
        "upstream_gate_status": text(upstream_gate.get("status")),
        "metrics": {
            "authority_object_row_count": len(_rows(model.get("business_objects"))),
            "object_node_count": len(object_nodes),
            "collapsed_duplicate_object_row_count": sum(
                int(row.get("duplicate_authority_rows_collapsed") or 0)
                for row in object_nodes
            ),
            "confirmed_object_node_count": sum(
                row.get("world_state") == "CONFIRMED" for row in object_nodes
            ),
            "conflicted_object_node_count": sum(
                row.get("world_state") == "CONFLICTED" for row in object_nodes
            ),
            "behavior_node_count": len(behavior_nodes),
            "suspected_behavior_node_count": sum(
                row.get("world_state") == "SUSPECTED" for row in behavior_nodes
            ),
            "edge_count": len(edges),
            "identity_hypothesis_count": len(hypotheses),
            "evidence_ref_count": len(evidence_registry),
            "integrity_violation_count": len(violations),
        },
    }
    return world


def project_business_world_model(asset: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(model)
    world = build_business_world_model(result)
    result["business_world_model"] = world
    asset["business_world_model"] = deepcopy(world)
    metrics = dict(as_dict(result.get("metrics")))
    metrics.update(
        {
            f"business_world_{key}": value
            for key, value in as_dict(as_dict(world.get("gate")).get("metrics")).items()
        }
    )
    result["metrics"] = metrics
    return result
