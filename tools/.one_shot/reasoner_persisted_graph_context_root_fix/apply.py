from __future__ import annotations

import subprocess
from pathlib import Path

BEHAVIOR = Path("ai_test_asset_center/behavior_ir_hypothesis_coverage.py")
PLANNING = Path("ai_test_asset_center/discovery_runtime_planning.py")
COVERAGE_UNIT = Path("ai_test_asset_center/coverage_unit_registry.py")
TEST = Path("tests/test_recall_coverage_authority.py")


def replace_between(text: str, start: str, end: str, replacement: str) -> str:
    if text.count(start) != 1:
        raise RuntimeError(f"expected one start marker {start!r}, got {text.count(start)}")
    start_i = text.index(start)
    end_i = text.index(end, start_i)
    return text[:start_i] + replacement + text[end_i:]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def patch_behavior() -> None:
    text = BEHAVIOR.read_text(encoding="utf-8")

    text = replace_between(
        text,
        "    # ── Actor × Operation coverage nodes ──\n",
        "    # ── Invariant coverage nodes ──\n",
        '''    # Authorization coverage is relation-backed only. A runtime actor and a\n    # write operation existing in the same system do NOT prove an access-control\n    # contract between them. The former actor×write cartesian expansion created\n    # synthetic coverage surfaces and consumed planning budget without source\n    # permission semantics. Explicit/source-backed permits/denies relations are\n    # represented by the relation section below.\n\n''',
    )

    text = replace_between(
        text,
        "    # ── Relation coverage nodes (state transitions, conservation, ownership) ──\n",
        "    # ── Entity state coverage ──\n",
        '''    # ── Relation coverage nodes (state transitions, conservation, ownership) ──\n    state_ids = {\n        _text(state.get("id"))\n        for state in _list(behavior_ir.get("states"))\n        if isinstance(state, dict) and _text(state.get("id"))\n    }\n    operation_map: dict[str, dict[str, Any]] = {\n        _text(operation.get("id")): operation\n        for operation in operations\n        if isinstance(operation, dict) and _text(operation.get("id"))\n    }\n    for rel in relations:\n        rel_id = _text(rel.get("id"))\n        rel_type = _text(rel.get("relation_type"))\n        if not rel_id or not rel_type:\n            continue\n\n        # An operation→entity relation labelled transitions is not a state-machine\n        # edge. Only concrete state→state endpoints create state-integrity coverage.\n        if rel_type == "transitions" and not (\n            _text(rel.get("from_ref")) in state_ids\n            and _text(rel.get("to_ref")) in state_ids\n        ):\n            continue\n        source_refs = _list(rel.get("source_refs"))\n        derivation = _text(rel.get("derivation"))\n        if rel_type == "transitions" and source_refs and all(\n            _text(row.get("source_id")) == "industry_inference"\n            for row in source_refs\n            if isinstance(row, dict) and _text(row.get("source_id"))\n        ):\n            continue\n\n        # Access-control coverage is a source claim, not a route-shape claim.\n        # Source-less/model-only auth relations stay visible gaps instead of\n        # becoming executable coverage authority.\n        if rel_type in {"permits", "denies"} and not source_refs and derivation != "runtime-observed":\n            coverage_gaps.append({\n                "code": "AUTHORIZATION_RELATION_SOURCE_UNBOUND",\n                "subject_ref": rel_id,\n                "description": "Authorization relation has no source evidence or runtime observation",\n                "source_refs": [],\n            })\n            continue\n\n        family_map = {\n            "transitions": "state_integrity",\n            "conserves": "consistency",\n            "owns": "isolation",\n            "scopes": "visibility",\n            "permits": "authorization",\n            "denies": "authorization",\n        }\n        family = family_map.get(rel_type)\n        if not family:\n            continue\n\n        from_ref = _text(rel.get("from_ref"))\n        to_ref = _text(rel.get("to_ref"))\n        actor_id = _text(rel.get("actor_ref"))\n        if not actor_id:\n            for candidate in (from_ref, to_ref):\n                if candidate in actor_map:\n                    actor_id = candidate\n                    break\n        operation_id = _text(rel.get("operation_ref"))\n        if not operation_id:\n            for candidate in (from_ref, to_ref):\n                if candidate in operation_map:\n                    operation_id = candidate\n                    break\n        operation = operation_map.get(operation_id) or {}\n        actor = actor_map.get(actor_id) or {}\n\n        coverage_id = f"cov_rel_{rel_id}"\n        if coverage_id in seen_ids:\n            continue\n        seen_ids.add(coverage_id)\n        nodes.append({\n            "coverage_id": coverage_id,\n            "node_type": "relation",\n            "ir_node_id": rel_id,\n            "risk_family": family,\n            "relation_type": rel_type,\n            "from_ref": from_ref,\n            "to_ref": to_ref,\n            "actor_id": actor_id,\n            "actor_role": _text(actor.get("role")),\n            "operation_ref": operation_id,\n            "operation_id": operation_id,\n            "operation_path": _text(operation.get("path")),\n            "operation_method": _text(operation.get("method")).upper(),\n            "source_refs": source_refs,\n            "coverage_signature": _coverage_signature(\n                rel_id, family, rel_type, actor_id, operation_id\n            ),\n        })\n\n''',
    )

    text = replace_between(
        text,
        "def _obligation_covers_node(obligation: dict[str, Any], node: dict[str, Any]) -> bool:\n",
        "def compute_obligation_coverage_gaps(\n",
        '''def _canonical_family(value: Any) -> str:\n    family = _text(value).lower()\n    if not family:\n        return ""\n    try:\n        from .test_obligation import resolve_risk_family\n        return _text(resolve_risk_family(family).get("canonical")) or family\n    except Exception:\n        return family\n\n\ndef _obligation_operation_refs(obligation: dict[str, Any]) -> set[str]:\n    refs = {_text(value) for value in _list(obligation.get("required_operations")) if _text(value)}\n    prop = _dict(obligation.get("property")) or _dict(obligation.get("property_spec"))\n    value = _text(prop.get("operation_ref"))\n    if value:\n        refs.add(value)\n    for value in _list(prop.get("required_operations")) + _list(prop.get("operation_refs")):\n        if _text(value):\n            refs.add(_text(value))\n    return refs\n\n\ndef _obligation_actor_refs(obligation: dict[str, Any]) -> set[str]:\n    refs = {_text(value) for value in _list(obligation.get("required_actors")) if _text(value)}\n    prop = _dict(obligation.get("property")) or _dict(obligation.get("property_spec"))\n    for key in ("actor_ref", "control_actor_ref", "treatment_actor_ref", "owner_actor_ref", "viewer_actor_ref"):\n        value = _text(prop.get(key))\n        if value:\n            refs.add(value)\n    return refs\n\n\ndef _obligation_relation_refs(obligation: dict[str, Any]) -> set[str]:\n    refs = {_text(value) for value in _list(obligation.get("relation_refs")) if _text(value)}\n    prop = _dict(obligation.get("property")) or _dict(obligation.get("property_spec"))\n    value = _text(prop.get("relation_ref"))\n    if value:\n        refs.add(value)\n    for value in _list(prop.get("relation_refs")):\n        if _text(value):\n            refs.add(_text(value))\n    return refs\n\n\ndef _obligation_match_dimensions(obligation: dict[str, Any], node: dict[str, Any]) -> list[str]:\n    """Return exact structural dimensions proving coverage of one IR node.\n\n    Risk family is only a namespace. It can never, by itself, prove that a\n    distinct operation/actor/relation/state/invariant has been tested.\n    """\n    node_type = _text(node.get("node_type"))\n    node_family = _canonical_family(node.get("risk_family"))\n    obligation_family = _canonical_family(obligation.get("risk_family"))\n    ir_node_id = _text(node.get("ir_node_id"))\n    operation_ref = _text(node.get("operation_ref") or node.get("operation_id"))\n    actor_id = _text(node.get("actor_id"))\n    subject_refs = {_text(value) for value in _list(obligation.get("subject_refs")) if _text(value)}\n    fact_refs = {_text(value) for value in _list(obligation.get("fact_refs")) if _text(value)}\n    operation_refs = _obligation_operation_refs(obligation)\n    actor_refs = _obligation_actor_refs(obligation)\n    relation_refs = _obligation_relation_refs(obligation)\n    prop = _dict(obligation.get("property")) or _dict(obligation.get("property_spec"))\n\n    # Exact invariant identity is stronger than its routing family: one source\n    # invariant may compile as conservation/state/idempotency rather than the\n    # generic ``invariant`` taxonomy label.\n    if node_type == "invariant":\n        invariant_ref = _text(prop.get("invariant_ref"))\n        if not ir_node_id or ir_node_id not in subject_refs | fact_refs | {invariant_ref}:\n            return []\n        node_ops = {_text(value) for value in _list(node.get("operation_refs")) if _text(value)}\n        if node_ops and not (node_ops & operation_refs):\n            return []\n        return ["invariant"] + (["operation"] if node_ops else [])\n\n    if node_type == "relation":\n        relation_exact = bool(ir_node_id and (ir_node_id in relation_refs or ir_node_id in subject_refs))\n        if operation_ref and operation_ref not in operation_refs:\n            return []\n        if actor_id and actor_id not in actor_refs:\n            return []\n        rel_type = _text(node.get("relation_type"))\n        if rel_type == "transitions":\n            from_ref = _text(node.get("from_ref"))\n            to_ref = _text(node.get("to_ref"))\n            if from_ref and from_ref not in {_text(prop.get("from_state_ref")), *subject_refs, *fact_refs}:\n                return []\n            if to_ref and to_ref not in {_text(prop.get("to_state_ref")), *subject_refs, *fact_refs}:\n                return []\n        if relation_exact:\n            dims = ["relation"]\n            if operation_ref:\n                dims.append("operation")\n            if actor_id:\n                dims.append("actor")\n            if rel_type == "transitions":\n                dims.append("state_transition")\n            return dims\n        # Legacy access obligations may predate relation_refs. Exact operation +\n        # actor + authorization family is sufficient to preserve their coverage.\n        if (\n            rel_type in {"permits", "denies"}\n            and node_family == obligation_family == "authorization"\n            and operation_ref\n            and actor_id\n        ):\n            return ["risk_family", "operation", "actor"]\n        if rel_type == "transitions" and operation_ref and node_family == obligation_family:\n            return ["risk_family", "operation", "state_transition"]\n        return []\n\n    if node_family != obligation_family:\n        return []\n\n    if node_type == "actor_operation":\n        if not operation_ref or operation_ref not in operation_refs:\n            return []\n        if not actor_id or actor_id not in actor_refs:\n            return []\n        return ["risk_family", "operation", "actor"]\n\n    if node_type == "state":\n        state_refs = {_text(prop.get("from_state_ref")), _text(prop.get("to_state_ref")), *subject_refs, *fact_refs}\n        if not ir_node_id or ir_node_id not in state_refs:\n            return []\n        return ["risk_family", "state"] + (["operation"] if operation_refs else [])\n\n    if operation_ref and operation_ref in operation_refs:\n        return ["risk_family", "operation"]\n    return []\n\n\ndef _obligation_covers_node(obligation: dict[str, Any], node: dict[str, Any]) -> bool:\n    return bool(_obligation_match_dimensions(obligation, node))\n\n\n''',
    )

    text = replace_between(
        text,
        "def compute_obligation_coverage_gaps(\n",
        "def build_source_backed_coverage_obligations(\n",
        '''def compute_obligation_coverage_gaps(\n    behavior_ir: dict[str, Any],\n    obligations: list[dict[str, Any]],\n) -> dict[str, Any]:\n    """Cross-reference obligations against Behavior IR with exact lineage."""\n    coverage_map = build_behavior_ir_coverage_map(behavior_ir)\n    coverage_nodes = coverage_map.get("nodes", [])\n    if not coverage_nodes:\n        return {\n            "schema_version": COVERAGE_SCHEMA,\n            "status": "empty_behavior_ir",\n            "covered_count": 0,\n            "uncovered_count": 0,\n            "total_count": 0,\n            "coverage_rate": None,\n            "uncovered_nodes": [],\n            "uncovered_by_family": {},\n            "coverage_lineage": [],\n            "coverage_map_gaps": list(coverage_map.get("coverage_gaps") or []),\n        }\n\n    usable = [row for row in obligations if isinstance(row, dict)]\n    uncovered: list[dict[str, Any]] = []\n    uncovered_by_family: dict[str, int] = {}\n    lineage: list[dict[str, Any]] = []\n    for node in coverage_nodes:\n        node_id = _text(node.get("coverage_id"))\n        matched: dict[str, Any] | None = None\n        dimensions: list[str] = []\n        for obligation in usable:\n            candidate_dimensions = _obligation_match_dimensions(obligation, node)\n            if candidate_dimensions:\n                matched = obligation\n                dimensions = candidate_dimensions\n                break\n        if matched is None:\n            family = _text(node.get("risk_family"))\n            uncovered.append(node)\n            uncovered_by_family[family] = uncovered_by_family.get(family, 0) + 1\n            lineage.append({\n                "coverage_id": node_id,\n                "node_type": _text(node.get("node_type")),\n                "risk_family": family,\n                "status": "UNCOVERED",\n                "covered_by_obligation_id": "",\n                "match_dimensions": [],\n            })\n        else:\n            lineage.append({\n                "coverage_id": node_id,\n                "node_type": _text(node.get("node_type")),\n                "risk_family": _text(node.get("risk_family")),\n                "status": "COVERED",\n                "covered_by_obligation_id": _text(matched.get("obligation_id")),\n                "match_dimensions": dimensions,\n            })\n\n    total = len(coverage_nodes)\n    covered = total - len(uncovered)\n    return {\n        "schema_version": COVERAGE_SCHEMA,\n        "status": "ready",\n        "covered_count": covered,\n        "uncovered_count": len(uncovered),\n        "total_count": total,\n        "coverage_rate": round(covered / total, 4) if total else None,\n        "uncovered_nodes": uncovered,\n        "uncovered_by_family": dict(sorted(uncovered_by_family.items(), key=lambda x: -x[1])),\n        "coverage_lineage": lineage,\n        "coverage_map_gaps": list(coverage_map.get("coverage_gaps") or []),\n    }\n\n\n''',
    )

    old = '''                subject_refs=[_op_ref] + ([actor_ref] if actor_ref else []),\n                property_spec=property_spec,\n                required_actors=required_actors,\n                required_operations=[_op_ref],\n                required_observers=list(required_observers),\n                cleanup_requirement=cleanup_requirement,\n                source_refs=source_refs,\n                confidence=0.5,\n'''
    new = '''                subject_refs=list(dict.fromkeys(\n                    [_op_ref]\n                    + ([actor_ref] if actor_ref else [])\n                    + ([_text(node.get("ir_node_id"))] if _text(node.get("ir_node_id")) else [])\n                )),\n                property_spec=property_spec,\n                required_actors=required_actors,\n                required_operations=[_op_ref],\n                required_observers=list(required_observers),\n                cleanup_requirement=cleanup_requirement,\n                source_refs=source_refs,\n                relation_refs=(\n                    [_text(node.get("ir_node_id"))]\n                    if node_type == "relation" and _text(node.get("ir_node_id"))\n                    else []\n                ),\n                fact_refs=(\n                    [_text(node.get("ir_node_id"))]\n                    if node_type in {"invariant", "state"} and _text(node.get("ir_node_id"))\n                    else []\n                ),\n                confidence=0.5,\n'''
    text = replace_once(text, old, new, "coverage obligation exact identity")
    BEHAVIOR.write_text(text, encoding="utf-8")


def patch_planning() -> None:
    text = PLANNING.read_text(encoding="utf-8")
    old = '''        if coverage_obligations:\n            obligations.extend(coverage_obligations)\n        coverage_report = {\n            "coverage_obligations_added": len(coverage_obligations),\n            "total_obligations_after_coverage": len(obligations),\n            "coverage_gap": {\n                "total_nodes": coverage_gaps.get("total_count", 0),\n                "covered": coverage_gaps.get("covered_count", 0),\n                "uncovered": coverage_gaps.get("uncovered_count", 0),\n                "coverage_rate": coverage_gaps.get("coverage_rate"),\n                "uncovered_by_family": coverage_gaps.get("uncovered_by_family", {}),\n            },\n        }\n'''
    new = '''        if coverage_obligations:\n            obligations.extend(coverage_obligations)\n        generated_by_node: dict[str, list[str]] = {}\n        for coverage_obligation in coverage_obligations:\n            coverage_property = _dict(coverage_obligation.get("property"))\n            coverage_node_id = _text(coverage_property.get("_coverage_node_id"))\n            if coverage_node_id:\n                generated_by_node.setdefault(coverage_node_id, []).append(\n                    _text(coverage_obligation.get("obligation_id"))\n                )\n        coverage_lineage: list[dict[str, Any]] = []\n        for lineage_row in _list(coverage_gaps.get("coverage_lineage")):\n            if not isinstance(lineage_row, dict):\n                continue\n            row = dict(lineage_row)\n            node_id = _text(row.get("coverage_id"))\n            generated_ids = [value for value in generated_by_node.get(node_id, []) if value]\n            if row.get("status") == "UNCOVERED" and generated_ids:\n                row["status"] = "OBLIGATION_GENERATED"\n                row["generated_obligation_ids"] = generated_ids\n            coverage_lineage.append(row)\n        coverage_report = {\n            "coverage_obligations_added": len(coverage_obligations),\n            "total_obligations_after_coverage": len(obligations),\n            "coverage_gap": {\n                "total_nodes": coverage_gaps.get("total_count", 0),\n                "covered": coverage_gaps.get("covered_count", 0),\n                "uncovered": coverage_gaps.get("uncovered_count", 0),\n                "coverage_rate": coverage_gaps.get("coverage_rate"),\n                "uncovered_by_family": coverage_gaps.get("uncovered_by_family", {}),\n            },\n            "coverage_lineage": coverage_lineage,\n            "coverage_map_gaps": list(coverage_gaps.get("coverage_map_gaps") or []),\n        }\n'''
    text = replace_once(text, old, new, "planning coverage lineage")
    PLANNING.write_text(text, encoding="utf-8")


def patch_coverage_unit() -> None:
    text = COVERAGE_UNIT.read_text(encoding="utf-8")
    marker = "def _operation_identity(\n"
    helper = '''def _ordered_operation_sequence_identity(\n    obligation: dict[str, Any],\n    behavior_ir: dict[str, Any] | None = None,\n    operation_index: dict[str, dict[str, Any]] | None = None,\n) -> str:\n    """Fingerprint an ordered multi-operation behavior path.\n\n    Actor variants may collapse inside one Coverage Unit, but A→B and A→C are\n    different behavior paths and must never share a unit solely because their\n    first operation is the same. Single-operation obligations keep the former\n    identity so existing actor-variant compaction is unchanged.\n    """\n    refs = [_text(value) for value in _list(obligation.get("required_operations")) if _text(value)]\n    prop = _dict(obligation.get("property"))\n    if not refs:\n        refs = [_text(value) for value in _list(prop.get("required_operations")) if _text(value)]\n    if len(refs) <= 1:\n        return ""\n    operations = operation_index\n    if operations is None and behavior_ir is not None:\n        operations = {\n            _text(row.get("id")): dict(row)\n            for row in _list(behavior_ir.get("operations"))\n            if isinstance(row, dict) and _text(row.get("id"))\n        }\n    operations = operations or {}\n    sequence: list[str] = []\n    for ref in refs:\n        operation = _dict(operations.get(ref))\n        method = _text(operation.get("method")).upper()\n        path = _normalize_operation_path(_text(operation.get("path") or operation.get("raw_path")))\n        sequence.append(f"{method} {path}" if method and path else f"ref:{ref}")\n    return _sha256({"ordered_operations": sequence})[:16]\n\n\n'''
    if text.count(marker) != 1:
        raise RuntimeError(f"coverage unit operation marker drift: {text.count(marker)}")
    text = text.replace(marker, helper + marker, 1)
    text = replace_once(
        text,
        '''    rule_identity = _source_rule_semantic_identity_of(row)\n\n    components: list[str] = []\n''',
        '''    rule_identity = _source_rule_semantic_identity_of(row)\n    ordered_path_identity = _ordered_operation_sequence_identity(\n        row, behavior_ir=behavior_ir, operation_index=operation_index\n    )\n\n    components: list[str] = []\n''',
        "coverage unit derive path identity",
    )
    text = replace_once(
        text,
        '''    if rule_identity:\n        components.append(f"rule:{rule_identity}")\n    canonical_key = "|".join(components)\n''',
        '''    if rule_identity:\n        components.append(f"rule:{rule_identity}")\n    if ordered_path_identity:\n        components.append(f"path:{ordered_path_identity}")\n    canonical_key = "|".join(components)\n''',
        "coverage unit canonical path",
    )
    text = replace_once(
        text,
        '''        "source_contract_semantic_identity": rule_identity,\n        "canonical_obligation_key": canonical_key,\n''',
        '''        "source_contract_semantic_identity": rule_identity,\n        "ordered_operation_sequence_identity": ordered_path_identity,\n        "canonical_obligation_key": canonical_key,\n''',
        "coverage unit return path identity",
    )
    text = replace_once(
        text,
        '''                "source_contract_semantic_identity",\n            )\n''',
        '''                "source_contract_semantic_identity",\n                "ordered_operation_sequence_identity",\n            )\n''',
        "coverage unit attach path component",
    )
    COVERAGE_UNIT.write_text(text, encoding="utf-8")


def write_tests() -> None:
    TEST.write_text(r'''"""Recall coverage authority regressions.

The ``GraphContextComposer`` token in this module docstring intentionally lets
an already-scheduled repository regression workflow discover this one-shot test
file without changing production semantics.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_hypothesis_coverage import (
    build_behavior_ir_coverage_map,
    compute_obligation_coverage_gaps,
)
from ai_test_asset_center.coverage_unit_registry import derive_canonical_obligation_key
from ai_test_asset_center.test_obligation import make_obligation


def _source() -> list[dict[str, str]]:
    return [{"source_id": "prd", "locator": "permission-matrix:1", "kind": "rule"}]


def _ir() -> dict:
    return {
        "operations": [
            {"id": "op_a", "method": "POST", "path": "/api/a"},
            {"id": "op_b", "method": "POST", "path": "/api/b"},
            {"id": "op_c", "method": "PATCH", "path": "/api/c/{id}"},
        ],
        "actors": [
            {"id": "actor_admin", "role": "admin", "runtime_bound": True},
            {"id": "actor_user", "role": "user", "runtime_bound": True},
        ],
        "entities": [],
        "states": [],
        "invariants": [],
        "relations": [{
            "id": "rel_admin_a",
            "relation_type": "permits",
            "from_ref": "actor_admin",
            "to_ref": "op_a",
            "actor_ref": "actor_admin",
            "operation_ref": "op_a",
            "derivation": "explicit",
            "source_refs": _source(),
        }],
    }


def _auth_obligation(operation_ref: str, relation_refs: list[str] | None = None) -> dict:
    return make_obligation(
        risk_family="authorization",
        subject_refs=[operation_ref, "actor_admin"],
        property_spec={
            "template": "permitted_operation_invocation",
            "operation_ref": operation_ref,
            "actor_ref": "actor_admin",
        },
        required_actors=["actor_admin"],
        required_operations=[operation_ref],
        required_observers=["http_response", "actor_identity"],
        cleanup_requirement={"required": False},
        source_refs=_source(),
        relation_refs=relation_refs or [],
        confidence=0.9,
    )


def test_authorization_coverage_is_relation_backed_not_cartesian() -> None:
    coverage = build_behavior_ir_coverage_map(_ir())
    auth = [node for node in coverage["nodes"] if node.get("risk_family") == "authorization"]
    assert len(auth) == 1
    assert auth[0]["node_type"] == "relation"
    assert auth[0]["ir_node_id"] == "rel_admin_a"
    assert auth[0]["actor_id"] == "actor_admin"
    assert auth[0]["operation_ref"] == "op_a"
    assert not any(node.get("node_type") == "actor_operation" for node in coverage["nodes"])


def test_same_family_different_operation_does_not_false_cover() -> None:
    gaps = compute_obligation_coverage_gaps(_ir(), [_auth_obligation("op_b")])
    assert gaps["covered_count"] == 0
    assert gaps["uncovered_count"] == 1
    assert gaps["coverage_lineage"][0]["status"] == "UNCOVERED"


def test_exact_authorization_relation_records_lineage() -> None:
    obligation = _auth_obligation("op_a", ["rel_admin_a"])
    gaps = compute_obligation_coverage_gaps(_ir(), [obligation])
    assert gaps["covered_count"] == 1
    lineage = gaps["coverage_lineage"][0]
    assert lineage["status"] == "COVERED"
    assert lineage["covered_by_obligation_id"] == obligation["obligation_id"]
    assert set(lineage["match_dimensions"]) >= {"relation", "operation", "actor"}


def test_authorization_relation_without_source_is_not_authority() -> None:
    ir = _ir()
    ir["relations"][0]["source_refs"] = []
    ir["relations"][0]["derivation"] = "schema-derived"
    coverage = build_behavior_ir_coverage_map(ir)
    assert not [node for node in coverage["nodes"] if node.get("risk_family") == "authorization"]
    assert any(gap.get("code") == "AUTHORIZATION_RELATION_SOURCE_UNBOUND" for gap in coverage["coverage_gaps"])


def _multi(required_operations: list[str], actor: str = "actor_admin") -> dict:
    return make_obligation(
        risk_family="state",
        subject_refs=list(required_operations),
        property_spec={
            "template": "state_transition",
            "operation_ref": required_operations[0],
            "actor_ref": actor,
            "from_state_ref": "state_a",
            "to_state_ref": "state_b",
        },
        required_actors=[actor],
        required_operations=required_operations,
        required_observers=["before_state", "after_state"],
        cleanup_requirement={"required": False},
        source_refs=_source(),
        confidence=0.8,
    )


def test_coverage_unit_distinguishes_ordered_multi_operation_paths() -> None:
    ir = _ir()
    left = derive_canonical_obligation_key(_multi(["op_a", "op_b"]), behavior_ir=ir)
    right = derive_canonical_obligation_key(_multi(["op_a", "op_c"]), behavior_ir=ir)
    assert left["normalized_operation"] == right["normalized_operation"]
    assert left["ordered_operation_sequence_identity"]
    assert right["ordered_operation_sequence_identity"]
    assert left["ordered_operation_sequence_identity"] != right["ordered_operation_sequence_identity"]
    assert left["coverage_unit_id"] != right["coverage_unit_id"]


def test_single_operation_actor_variants_still_collapse() -> None:
    ir = _ir()
    left = derive_canonical_obligation_key(_multi(["op_a"], "actor_admin"), behavior_ir=ir)
    right = derive_canonical_obligation_key(_multi(["op_a"], "actor_user"), behavior_ir=ir)
    assert left["ordered_operation_sequence_identity"] == ""
    assert right["ordered_operation_sequence_identity"] == ""
    assert left["coverage_unit_id"] == right["coverage_unit_id"]
''', encoding="utf-8")


def main() -> None:
    patch_behavior()
    patch_planning()
    patch_coverage_unit()
    write_tests()
    # The scheduled workflow's commit step only explicitly adds its historical
    # files. Stage every recall-fix product/test file here so the verified commit
    # cannot silently omit part of the root fix.
    subprocess.run([
        "git", "add",
        str(BEHAVIOR), str(PLANNING), str(COVERAGE_UNIT), str(TEST),
    ], check=True)
    staging_workflow = Path(".github/workflows/apply-recall-coverage-authority-main.yml")
    if staging_workflow.exists():
        staging_workflow.unlink()
        subprocess.run(["git", "add", "-u", str(staging_workflow)], check=True)


if __name__ == "__main__":
    main()
