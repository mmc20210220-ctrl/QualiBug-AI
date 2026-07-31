"""Govern Runtime Materialization with canonical mandatory-outcome identity.

The private mechanics module retains the existing environment, identity and cleanup repairs.
This public authority adds a deterministic Runtime Plan oracle-template to assertion-draft binding
and fails closed when any mandatory ``outcome_ref`` loses its runtime assertion identity.
"""
from __future__ import annotations

from typing import Any

from . import _runtime_materialization_governance_mechanics as _core
from .schema import as_dict, as_list, stable_id, text, unique_text


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _plan_index(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }


def _bind_assertion_outcomes(
    asset: dict[str, Any], materialization: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated = dict(materialization)
    materialization_id = text(updated.get("materialization_id"))
    plan = _plan_index(asset).get(text(updated.get("runtime_plan_ref"))) or {}
    oracle = as_dict(plan.get("oracle_query_templates"))
    templates = _dicts(oracle.get("templates"))
    by_draft_id = {
        stable_id("assertion_draft", materialization_id, row.get("template_id")): row
        for row in templates
        if text(row.get("template_id"))
    }
    assertions: list[dict[str, Any]] = []
    covered: list[str] = []
    for raw in _dicts(updated.get("assertion_drafts")):
        row = dict(raw)
        template = by_draft_id.get(text(row.get("draft_id"))) or {}
        semantic_role = text(template.get("semantic_role"))
        outcome_ref = text(template.get("outcome_ref"))
        row.update(
            {
                "oracle_template_ref": template.get("template_id"),
                "semantic_role": semantic_role,
                "predicate_ref": template.get("predicate_ref"),
                "outcome_ref": outcome_ref,
                "outcome_type": template.get("outcome_type"),
                "assertion_requirement_ref": template.get(
                    "assertion_requirement_ref"
                ),
                "canonical_outcome_identity_bound": bool(
                    semantic_role == "MANDATORY_OUTCOME" and outcome_ref
                ),
            }
        )
        if semantic_role == "MANDATORY_OUTCOME" and outcome_ref:
            covered.append(outcome_ref)
        assertions.append(row)
    required = unique_text(as_list(oracle.get("mandatory_outcome_refs")))
    strict = bool(plan.get("canonical_outcome_identity_required")) or bool(
        as_dict(asset.get("runtime_plan_gate")).get(
            "canonical_outcome_identity_required"
        )
    )
    covered = unique_text(covered)
    missing = sorted(set(required) - set(covered))
    updated.update(
        {
            "assertion_drafts": assertions,
            "mandatory_outcome_refs": required,
            "covered_mandatory_outcome_refs": covered,
            "missing_mandatory_outcome_refs": missing,
            "canonical_outcome_identity_enforced": strict,
            "outcome_assertion_identity_complete": (
                bool(required) and not missing if strict else not missing
            ),
        }
    )
    unknowns = [
        {
            "unknown_id": stable_id(
                "runtime_materialization_unknown",
                materialization_id,
                "RUNTIME_MATERIALIZATION_OUTCOME_ASSERTION_DRAFT_UNRESOLVED",
                outcome_ref,
            ),
            "kind": "RUNTIME_MATERIALIZATION_OUTCOME_ASSERTION_DRAFT_UNRESOLVED",
            "reason_code": "RUNTIME_MATERIALIZATION_OUTCOME_ASSERTION_DRAFT_UNRESOLVED",
            "runtime_materialization_ref": materialization_id,
            "outcome_ref": outcome_ref,
            "blocks_runtime_materialization": True,
            "execution_allowed": False,
        }
        for outcome_ref in missing
    ]
    if strict and not required:
        unknowns.append(
            {
                "unknown_id": stable_id(
                    "runtime_materialization_unknown",
                    materialization_id,
                    "RUNTIME_MATERIALIZATION_CANONICAL_OUTCOME_REFS_MISSING",
                ),
                "kind": "RUNTIME_MATERIALIZATION_CANONICAL_OUTCOME_REFS_MISSING",
                "reason_code": "RUNTIME_MATERIALIZATION_CANONICAL_OUTCOME_REFS_MISSING",
                "runtime_materialization_ref": materialization_id,
                "blocks_runtime_materialization": True,
                "execution_allowed": False,
            }
        )
    return updated, unknowns


def _rebuild_outcome_gate(asset: dict[str, Any], model: dict[str, Any]) -> None:
    materializations = _dicts(asset.get("runtime_materializations"))
    unknowns = _dicts(asset.get("runtime_materialization_unknowns"))
    rebuilt: list[dict[str, Any]] = []
    for raw in materializations:
        row = dict(raw)
        materialization_id = text(row.get("materialization_id"))
        related = [
            item
            for item in unknowns
            if text(item.get("runtime_materialization_ref")) == materialization_id
        ]
        blocked = any(
            bool(item.get("blocks_runtime_materialization")) for item in related
        )
        row.update(
            {
                "status": "INCOMPLETE" if blocked else "DRAFT_READY",
                "formal_runtime_materialization": not blocked,
                "unresolved_materialization_semantics": unique_text(
                    item.get("reason_code") for item in related
                ),
                "execution_allowed": False,
                "request_sendable": False,
                "network_calls_allowed": False,
                "assertions_executable": False,
                "bug_classification_allowed": False,
            }
        )
        request = dict(as_dict(row.get("request_draft")))
        request.update(
            {
                "draft_compiled": not blocked,
                "request_serialized": False,
                "request_sendable": False,
                "network_call_allowed": False,
            }
        )
        row["request_draft"] = request
        rebuilt.append(row)
    asset["runtime_materializations"] = rebuilt
    model["runtime_materializations"] = [dict(row) for row in rebuilt]
    ready = sum(1 for row in rebuilt if text(row.get("status")) == "DRAFT_READY")
    incomplete = len(rebuilt) - ready
    previous = as_dict(asset.get("runtime_materialization_gate"))
    status = (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
        if incomplete
        else "PASS"
        if rebuilt
        else text(previous.get("status")) or "NO_RUNTIME_MATERIALIZATION_COMPILED"
    )
    metrics = dict(as_dict(previous.get("metrics")))
    metrics.update(
        {
            "runtime_materialization_count": len(rebuilt),
            "ready_runtime_materialization_count": ready,
            "incomplete_runtime_materialization_count": incomplete,
            "runtime_materialization_unknown_count": len(unknowns),
            "mandatory_outcome_ref_count": sum(
                len(as_list(row.get("mandatory_outcome_refs"))) for row in rebuilt
            ),
            "covered_mandatory_outcome_ref_count": sum(
                len(as_list(row.get("covered_mandatory_outcome_refs")))
                for row in rebuilt
            ),
        }
    )
    gate = {
        **previous,
        "status": status,
        "entry_allowed": status == "PASS",
        "runtime_materialization_ready": status == "PASS",
        "execution_allowed": False,
        "request_sendable": False,
        "network_calls_allowed": False,
        "assertions_executable": False,
        "bug_classification_allowed": False,
        "canonical_outcome_identity_required": bool(
            as_dict(asset.get("runtime_plan_gate")).get(
                "canonical_outcome_identity_required"
            )
        ),
        "metrics": metrics,
    }
    asset["runtime_materialization_gate"] = gate
    model["runtime_materialization_gate"] = dict(gate)

    accepted = {
        text(row.get("materialization_id"))
        for row in rebuilt
        if text(row.get("status")) == "DRAFT_READY"
    }
    relationships = [
        dict(row)
        for row in as_list(asset.get("relationships"))
        if isinstance(row, dict)
        and text(row.get("relation"))
        != "runtime_materialization_to_mandatory_outcome"
    ]
    for materialization in rebuilt:
        materialization_id = text(materialization.get("materialization_id"))
        accepted_materialization = materialization_id in accepted
        for outcome_ref in as_list(
            materialization.get("covered_mandatory_outcome_refs")
        ):
            relationships.append(
                {
                    "edge_id": stable_id(
                        "edge",
                        "runtime_materialization_to_mandatory_outcome",
                        materialization_id,
                        outcome_ref,
                    ),
                    "from": materialization_id,
                    "to": outcome_ref,
                    "relation": "runtime_materialization_to_mandatory_outcome",
                    "status": "accepted"
                    if accepted_materialization
                    else "candidate",
                    "confidence": 1.0 if accepted_materialization else 0.0,
                    "derivation": "canonical_outcome_assertion_binding",
                    "evidence": {"execution_allowed": False},
                }
            )
    relationships = list(
        {
            text(row.get("edge_id")): row
            for row in relationships
            if text(row.get("edge_id"))
        }.values()
    )
    asset["relationships"] = relationships
    asset["runtime_materialization_relationships"] = [
        row
        for row in relationships
        if text(row.get("relation"))
        in {
            "runtime_plan_to_materialization",
            "runtime_materialization_to_mandatory_outcome",
        }
    ]
    model["runtime_materialization_relationships"] = [
        dict(row) for row in asset["runtime_materialization_relationships"]
    ]
    projected = {
        "runtime_materialization_status": status,
        "runtime_materialization_ready": status == "PASS",
        "runtime_materialization_count": len(rebuilt),
        "runtime_materialization_incomplete_count": incomplete,
        "runtime_materialization_unknown_count": len(unknowns),
        "runtime_materialization_relationship_count": len(
            asset["runtime_materialization_relationships"]
        ),
        "runtime_materialization_mandatory_outcome_ref_count": int(
            metrics.get("mandatory_outcome_ref_count") or 0
        ),
        "runtime_materialization_covered_outcome_ref_count": int(
            metrics.get("covered_mandatory_outcome_ref_count") or 0
        ),
        "materialized_execution_allowed": False,
    }
    summary = dict(as_dict(asset.get("summary")))
    summary.update(projected)
    asset["summary"] = summary
    source_summary = dict(as_dict(model.get("source_summary")))
    source_summary.update(projected)
    model["source_summary"] = source_summary
    model_metrics = dict(as_dict(model.get("metrics")))
    model_metrics.update(projected)
    model["metrics"] = model_metrics


def project_governed_runtime_materializations_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    _core.project_governed_runtime_materializations_to_asset(asset, model)
    governed: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("runtime_materializations")):
        row, unknowns = _bind_assertion_outcomes(asset, raw)
        governed.append(row)
        added.extend(unknowns)
    existing = _dicts(asset.get("runtime_materialization_unknowns"))
    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in [*existing, *added]
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["runtime_materializations"] = governed
    asset["runtime_materialization_unknowns"] = all_unknowns
    model["runtime_materializations"] = [dict(row) for row in governed]
    model["runtime_materialization_unknowns"] = [dict(row) for row in all_unknowns]
    _rebuild_outcome_gate(asset, model)
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_materialization_assertions_require_explicit_outcome_ref": True,
            "runtime_materialization_condition_and_outcome_drafts_are_separate": True,
            "runtime_materialization_missing_outcome_draft_fails_closed": True,
            "runtime_materialization_legacy_assertion_aggregation_authoritative": False,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["project_governed_runtime_materializations_to_asset"]
