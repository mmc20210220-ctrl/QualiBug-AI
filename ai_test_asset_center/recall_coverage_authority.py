"""Exact, source-backed coverage authority for Behavior IR Recall.

Coverage is a planning authority: declaring a surface COVERED suppresses gap
fill and therefore suppresses execution.  This module keeps that decision
structural and fail-closed.  It never treats a shared risk-family label as proof
that an obligation exercised a particular operation, actor, relation, state, or
invariant.
"""
from __future__ import annotations

from typing import Any, Callable


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _property(obligation: dict[str, Any]) -> dict[str, Any]:
    return _dict(obligation.get("property")) or _dict(obligation.get("property_spec"))


def _canonical_family(value: Any) -> str:
    family = _text(value).lower()
    if not family:
        return ""
    try:
        from .test_obligation import resolve_risk_family

        return _text(_dict(resolve_risk_family(family)).get("canonical")) or family
    except Exception:
        return family


def harden_behavior_ir_coverage_map(
    base_map: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Drop synthetic auth cross-products; retain source-backed auth relations.

    Actor×write-operation coexistence is not a permission contract.  A permits
    or denies relation becomes authorization coverage only when it is accepted
    and carries source evidence, or is explicitly runtime-observed.
    """
    result = dict(base_map) if isinstance(base_map, dict) else {}
    relations = {
        _text(row.get("id")): row
        for row in _list(behavior_ir.get("relations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    operations = {
        _text(row.get("id")): row
        for row in _list(behavior_ir.get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    actors = {
        _text(row.get("id")): row
        for row in _list(behavior_ir.get("actors"))
        if isinstance(row, dict) and _text(row.get("id"))
    }

    nodes: list[dict[str, Any]] = []
    gaps = [
        dict(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
    ]
    seen: set[str] = set()

    for raw in _list(result.get("nodes")):
        if not isinstance(raw, dict):
            continue
        node = dict(raw)
        family = _text(node.get("risk_family"))
        node_type = _text(node.get("node_type"))

        # The legacy map emits every runtime actor × write operation.  Those
        # nodes carry no source decision and must not consume coverage budget.
        if family == "authorization" and node_type == "actor_operation":
            continue

        if family == "authorization" and node_type == "relation":
            relation = relations.get(_text(node.get("ir_node_id"))) or {}
            rel_type = _text(relation.get("relation_type") or node.get("relation_type"))
            if rel_type in {"permits", "denies"}:
                status = _text(relation.get("status")).lower()
                derivation = _text(relation.get("derivation")).lower()
                source_refs = [
                    dict(ref)
                    for ref in _list(relation.get("source_refs"))
                    if isinstance(ref, dict)
                ]
                if status and status != "accepted":
                    gaps.append({
                        "code": "AUTHORIZATION_RELATION_NOT_ACCEPTED",
                        "subject_ref": _text(relation.get("id")),
                        "status": status,
                        "source_refs": source_refs,
                    })
                    continue
                if not source_refs and derivation != "runtime-observed":
                    gaps.append({
                        "code": "AUTHORIZATION_RELATION_SOURCE_UNBOUND",
                        "subject_ref": _text(relation.get("id")),
                        "source_refs": [],
                    })
                    continue

                from_ref = _text(relation.get("from_ref") or node.get("from_ref"))
                to_ref = _text(relation.get("to_ref") or node.get("to_ref"))
                actor_id = _text(relation.get("actor_ref")) or next(
                    (ref for ref in (from_ref, to_ref) if ref in actors), ""
                )
                operation_id = _text(relation.get("operation_ref")) or next(
                    (ref for ref in (from_ref, to_ref) if ref in operations), ""
                )
                if not actor_id or not operation_id:
                    gaps.append({
                        "code": "AUTHORIZATION_RELATION_IDENTITY_UNBOUND",
                        "subject_ref": _text(relation.get("id")),
                        "source_refs": source_refs,
                    })
                    continue
                operation = operations.get(operation_id) or {}
                actor = actors.get(actor_id) or {}
                node.update({
                    "relation_type": rel_type,
                    "from_ref": from_ref,
                    "to_ref": to_ref,
                    "actor_id": actor_id,
                    "actor_role": _text(actor.get("role")),
                    "operation_ref": operation_id,
                    "operation_id": operation_id,
                    "operation_path": _text(operation.get("path")),
                    "operation_method": _text(operation.get("method")).upper(),
                    "source_refs": source_refs,
                })

        coverage_id = _text(node.get("coverage_id"))
        if coverage_id and coverage_id in seen:
            continue
        if coverage_id:
            seen.add(coverage_id)
        nodes.append(node)

    counts: dict[str, int] = {}
    for node in nodes:
        family = _text(node.get("risk_family"))
        counts[family] = counts.get(family, 0) + 1
    result["nodes"] = nodes
    result["node_count"] = len(nodes)
    result["risk_family_counts"] = dict(sorted(counts.items()))
    result["coverage_gaps"] = gaps
    return result


def obligation_operation_refs(obligation: dict[str, Any]) -> set[str]:
    refs = {
        _text(value)
        for value in _list(obligation.get("required_operations"))
        if _text(value)
    }
    prop = _property(obligation)
    for value in [prop.get("operation_ref"), *_list(prop.get("operation_refs"))]:
        if _text(value):
            refs.add(_text(value))
    return refs


def obligation_actor_refs(obligation: dict[str, Any]) -> set[str]:
    refs = {
        _text(value)
        for value in _list(obligation.get("required_actors"))
        if _text(value)
    }
    prop = _property(obligation)
    for key in (
        "actor_ref",
        "control_actor_ref",
        "treatment_actor_ref",
        "owner_actor_ref",
        "viewer_actor_ref",
        "fixture_owner_actor_ref",
    ):
        if _text(prop.get(key)):
            refs.add(_text(prop.get(key)))
    return refs


def obligation_relation_refs(obligation: dict[str, Any]) -> set[str]:
    refs = {
        _text(value)
        for value in _list(obligation.get("relation_refs"))
        if _text(value)
    }
    prop = _property(obligation)
    for value in [prop.get("relation_ref"), *_list(prop.get("relation_refs"))]:
        if _text(value):
            refs.add(_text(value))
    return refs


def obligation_fact_refs(obligation: dict[str, Any]) -> set[str]:
    refs = {
        _text(value)
        for value in _list(obligation.get("fact_refs"))
        if _text(value)
    }
    refs.update(
        _text(value)
        for value in _list(_property(obligation).get("fact_refs"))
        if _text(value)
    )
    return refs


def obligation_match_dimensions(
    obligation: dict[str, Any], node: dict[str, Any]
) -> list[str]:
    """Return exact dimensions proving that one obligation covers one node."""
    if not isinstance(obligation, dict) or not isinstance(node, dict):
        return []

    node_type = _text(node.get("node_type"))
    node_family = _canonical_family(node.get("risk_family"))
    obligation_family = _canonical_family(obligation.get("risk_family"))
    ir_node_id = _text(node.get("ir_node_id"))
    operation_ref = _text(node.get("operation_ref") or node.get("operation_id"))
    actor_id = _text(node.get("actor_id"))
    operations = obligation_operation_refs(obligation)
    actors = obligation_actor_refs(obligation)
    relations = obligation_relation_refs(obligation)
    facts = obligation_fact_refs(obligation)
    subjects = {
        _text(value)
        for value in _list(obligation.get("subject_refs"))
        if _text(value)
    }
    prop = _property(obligation)

    if node_type == "invariant":
        invariant_ref = _text(prop.get("invariant_ref"))
        if not ir_node_id or ir_node_id not in subjects | facts | {invariant_ref}:
            return []
        node_ops = {
            _text(value)
            for value in _list(node.get("operation_refs"))
            if _text(value)
        }
        if node_ops and not (node_ops & operations):
            return []
        return ["invariant"] + (["operation"] if node_ops else [])

    if node_type == "relation":
        relation_type = _text(node.get("relation_type"))
        relation_exact = bool(ir_node_id and ir_node_id in relations | subjects | facts)
        if operation_ref and operation_ref not in operations:
            return []
        if actor_id and actor_id not in actors:
            return []
        if relation_type == "transitions":
            from_ref = _text(node.get("from_ref"))
            to_ref = _text(node.get("to_ref"))
            from_candidates = subjects | facts | {_text(prop.get("from_state_ref"))}
            to_candidates = subjects | facts | {_text(prop.get("to_state_ref"))}
            if from_ref and from_ref not in from_candidates:
                return []
            if to_ref and to_ref not in to_candidates:
                return []
        if relation_exact:
            dimensions = ["relation"]
            if operation_ref:
                dimensions.append("operation")
            if actor_id:
                dimensions.append("actor")
            if relation_type == "transitions":
                dimensions.append("state_transition")
            return dimensions
        # Legacy compatibility is exact on operation + actor; family alone is
        # never coverage authority.
        if (
            relation_type in {"permits", "denies"}
            and node_family == obligation_family == "authorization"
            and operation_ref
            and operation_ref in operations
            and actor_id
            and actor_id in actors
        ):
            return ["risk_family", "operation", "actor"]
        if (
            relation_type == "transitions"
            and node_family == obligation_family
            and operation_ref
            and operation_ref in operations
        ):
            return ["risk_family", "operation", "state_transition"]
        return []

    if node_family != obligation_family:
        return []
    if node_type == "actor_operation":
        if not operation_ref or operation_ref not in operations:
            return []
        if not actor_id or actor_id not in actors:
            return []
        return ["risk_family", "operation", "actor"]
    if node_type == "state":
        state_refs = subjects | facts | {
            _text(prop.get("from_state_ref")),
            _text(prop.get("to_state_ref")),
        }
        if not ir_node_id or ir_node_id not in state_refs:
            return []
        return ["risk_family", "state"] + (["operation"] if operations else [])
    if operation_ref and operation_ref in operations:
        return ["risk_family", "operation"]
    return []


def compute_exact_obligation_coverage_gaps(
    behavior_ir: dict[str, Any],
    obligations: list[dict[str, Any]],
    *,
    build_coverage_map: Callable[[dict[str, Any]], dict[str, Any]],
    schema_version: str,
) -> dict[str, Any]:
    coverage_map = build_coverage_map(behavior_ir)
    nodes = [row for row in _list(coverage_map.get("nodes")) if isinstance(row, dict)]
    if not nodes:
        return {
            "schema_version": schema_version,
            "status": "empty_behavior_ir",
            "covered_count": 0,
            "uncovered_count": 0,
            "total_count": 0,
            "coverage_rate": None,
            "uncovered_nodes": [],
            "uncovered_by_family": {},
            "coverage_lineage": [],
            "coverage_map_gaps": list(coverage_map.get("coverage_gaps") or []),
        }

    usable = [row for row in obligations if isinstance(row, dict)]
    uncovered: list[dict[str, Any]] = []
    by_family: dict[str, int] = {}
    lineage: list[dict[str, Any]] = []

    for node in nodes:
        matched: dict[str, Any] | None = None
        dimensions: list[str] = []
        for obligation in usable:
            candidate = obligation_match_dimensions(obligation, node)
            if candidate:
                matched = obligation
                dimensions = candidate
                break

        common = {
            "coverage_id": _text(node.get("coverage_id")),
            "node_type": _text(node.get("node_type")),
            "risk_family": _text(node.get("risk_family")),
            "source_refs": [
                dict(ref)
                for ref in _list(node.get("source_refs"))
                if isinstance(ref, dict)
            ],
        }
        if matched is None:
            family = _text(node.get("risk_family"))
            uncovered.append(node)
            by_family[family] = by_family.get(family, 0) + 1
            lineage.append({
                **common,
                "status": "UNCOVERED",
                "covered_by_obligation_id": "",
                "match_dimensions": [],
            })
        else:
            lineage.append({
                **common,
                "status": "COVERED",
                "covered_by_obligation_id": _text(matched.get("obligation_id")),
                "match_dimensions": dimensions,
            })

    total = len(nodes)
    covered = total - len(uncovered)
    return {
        "schema_version": schema_version,
        "status": "ready",
        "covered_count": covered,
        "uncovered_count": len(uncovered),
        "total_count": total,
        "coverage_rate": round(covered / total, 4) if total else None,
        "uncovered_nodes": uncovered,
        "uncovered_by_family": dict(
            sorted(by_family.items(), key=lambda item: (-item[1], item[0]))
        ),
        "coverage_lineage": lineage,
        "coverage_map_gaps": list(coverage_map.get("coverage_gaps") or []),
    }


def attach_coverage_origin(
    obligations: list[dict[str, Any]], gaps: dict[str, Any]
) -> list[dict[str, Any]]:
    """Carry the exact uncovered node identity into each gap-fill obligation."""
    nodes = {
        _text(row.get("coverage_id")): row
        for row in _list(gaps.get("uncovered_nodes"))
        if isinstance(row, dict) and _text(row.get("coverage_id"))
    }
    output: list[dict[str, Any]] = []
    for raw in obligations:
        obligation = dict(raw) if isinstance(raw, dict) else {}
        prop_key = "property" if isinstance(obligation.get("property"), dict) else "property_spec"
        prop = dict(_dict(obligation.get(prop_key)))
        coverage_id = _text(
            prop.get("_coverage_node_id") or obligation.get("_coverage_node_id")
        )
        node = nodes.get(coverage_id)
        if node:
            ir_node_id = _text(node.get("ir_node_id"))
            if ir_node_id:
                subjects = [
                    _text(value)
                    for value in _list(obligation.get("subject_refs"))
                    if _text(value)
                ]
                if ir_node_id not in subjects:
                    subjects.append(ir_node_id)
                obligation["subject_refs"] = subjects
                if _text(node.get("node_type")) == "relation":
                    refs = [
                        _text(value)
                        for value in _list(obligation.get("relation_refs"))
                        if _text(value)
                    ]
                    if ir_node_id not in refs:
                        refs.append(ir_node_id)
                    obligation["relation_refs"] = refs
                elif _text(node.get("node_type")) in {"invariant", "state"}:
                    refs = [
                        _text(value)
                        for value in _list(obligation.get("fact_refs"))
                        if _text(value)
                    ]
                    if ir_node_id not in refs:
                        refs.append(ir_node_id)
                    obligation["fact_refs"] = refs
            prop["_coverage_node_id"] = coverage_id
            prop["_coverage_origin_status"] = "SOURCE_BACKED_GAP_FILL"
            obligation[prop_key] = prop
        output.append(obligation)
    return output
