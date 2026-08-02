"""Close the Chinese comprehension → rule binding → Oracle/Probe mainline.

The first stage turns Chinese source spans into accepted or pending facts. This
stage only advances accepted facts that have an authoritative operation binding.
It never falls back to an arbitrary endpoint for a newly understood Chinese rule.
"""
from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Any


DOWNSTREAM_GATE_SCHEMA = "qualibug.chinese-business-downstream-gate.v1"
_READY = "READY_AUTHORITATIVE_OPERATION_BOUND"
_BLOCKED = "BLOCKED_NO_AUTHORITATIVE_OPERATION_LINK"
_IMPLEMENTATION_BLOCKED = "BLOCKED_IMPLEMENTATION_BINDING_GATE"
_UPSTREAM_BLOCKED = "BLOCKED_UPSTREAM_BUSINESS_COMPREHENSION_GATE"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _promoted_chinese_rules(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict)
        and _text(row.get("derivation")) == "chinese_first_business_comprehension"
        and _text(_dict(row.get("semantic_contract")).get("status")) == "ACCEPTED"
    ]


def _ensure_final_scenario_planning_gate(asset: dict[str, Any]) -> dict[str, Any]:
    """Build the generic final gate when the installed chain has not projected it yet."""
    from .enterprise_understanding.implementation_binding_projection import (
        SCENARIO_PLANNING_GATE_SCHEMA,
        project_final_scenario_planning_gate,
    )

    model = _dict(asset.get("enterprise_understanding_model"))
    if model:
        project_final_scenario_planning_gate(asset, model)
        return _dict(asset.get("scenario_planning_gate"))

    existing = _dict(asset.get("scenario_planning_gate"))
    if _text(existing.get("schema")) == SCENARIO_PLANNING_GATE_SCHEMA:
        return existing

    semantic_gate = _dict(asset.get("enterprise_comprehension_gate"))
    implementation_gate = _dict(asset.get("implementation_binding_gate"))
    semantic_ready = bool(semantic_gate.get("entry_allowed", True))
    implementation_status = _text(implementation_gate.get("status")) or "NOT_BUILT"
    implementation_ready = bool(
        implementation_gate.get("scenario_planning_allowed")
        or implementation_gate.get("entry_allowed")
    )
    if not semantic_ready:
        status = "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"
    elif implementation_status.startswith("BLOCKED"):
        status = implementation_status
    elif not implementation_ready:
        status = "PARTIAL_SCENARIO_PLANNING_IMPLEMENTATION_BINDING"
    else:
        status = "PASS"
    ready = status == "PASS"
    gate = {
        "schema": SCENARIO_PLANNING_GATE_SCHEMA,
        "status": status,
        "entry_allowed": ready,
        "scenario_planning_allowed": ready,
        "execution_allowed": False,
        "semantic_understanding_status": _text(semantic_gate.get("status")) or "UNKNOWN",
        "semantic_understanding_ready": semantic_ready,
        "implementation_binding_status": implementation_status,
        "implementation_binding_ready": implementation_ready,
        "implementation_binding_metrics": dict(
            _dict(implementation_gate.get("metrics"))
        ),
        "blocking_reasons": [
            reason
            for reason in (
                "SEMANTIC_UNDERSTANDING_NOT_CLOSED" if not semantic_ready else "",
                (
                    "IMPLEMENTATION_BINDING_NOT_CLOSED"
                    if semantic_ready and not implementation_ready
                    else ""
                ),
                (
                    "IMPLEMENTATION_BINDING_CONFLICT"
                    if implementation_status.startswith("BLOCKED")
                    else ""
                ),
            )
            if reason
        ],
        "request_payload_compiled": False,
        "expected_assertion_compiled": False,
        "runtime_environment_validated": False,
        "quality_claim": "SCENARIO_PLANNING_ENTRY_CLOSURE_NOT_RUNTIME_EXECUTABILITY",
    }
    asset["scenario_planning_gate"] = gate
    return gate


def refresh_chinese_business_downstream(
    asset: dict[str, Any],
    *,
    max_probe_count: int = 140,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bind accepted Chinese rules and rebuild downstream artifacts safely."""
    from . import _api

    base_scenario_gate = _ensure_final_scenario_planning_gate(asset)
    rules = [dict(row) for row in _list(asset.get("rule_library")) if isinstance(row, dict)]
    promoted = _promoted_chinese_rules(asset)
    interfaces = [dict(row) for row in _list(asset.get("interfaces")) if isinstance(row, dict)]
    existing_relationships = [
        dict(row) for row in _list(asset.get("relationships")) if isinstance(row, dict)
    ]

    authoritative_edges = _api._authoritative_rule_to_interface_edges(promoted, interfaces)
    relationships = _api._dedupe_by_id(
        [*existing_relationships, *authoritative_edges],
        "edge_id",
    )
    promoted_rule_ids = {
        _text(row.get("rule_id")) for row in promoted if _text(row.get("rule_id"))
    }
    # Consume every authoritative rule→interface edge for promoted Chinese rules,
    # including edges that already lived on the asset before this refresh.
    accepted_operation_edges: dict[str, list[str]] = {}
    for rule_id in promoted_rule_ids:
        operation_ids = sorted(
            {
                _text(candidate.get("to"))
                for candidate in relationships
                if isinstance(candidate, dict)
                and _text(candidate.get("from")) == rule_id
                and _text(candidate.get("relation")) == "rule_to_interface"
                and _api._relationship_is_authoritative(candidate)
                and _text(candidate.get("to"))
            }
        )
        if operation_ids:
            accepted_operation_edges[rule_id] = operation_ids

    ready_rule_ids: set[str] = set()
    bound_for_behavior_ir_rule_ids: set[str] = set()
    blocked_rules: list[dict[str, Any]] = []
    for rule in rules:
        if _text(rule.get("derivation")) != "chinese_first_business_comprehension":
            continue
        rule_id = _text(rule.get("rule_id"))
        operation_ids = accepted_operation_edges.get(rule_id, [])
        if operation_ids:
            ready_rule_ids.add(rule_id)
            bound_for_behavior_ir_rule_ids.add(rule_id)
            rule["downstream_binding_status"] = _READY
            rule["authoritative_operation_refs"] = operation_ids
            # Behavior IR reads operation_refs; keep the authoritative bind visible
            # even when scenario/probe mainline later re-blocks ready_rule_ids.
            existing_op_refs = [
                _text(value)
                for value in _list(rule.get("operation_refs"))
                if _text(value)
            ]
            rule["operation_refs"] = list(
                dict.fromkeys([*existing_op_refs, *operation_ids])
            )
        else:
            rule["downstream_binding_status"] = _BLOCKED
            rule["authoritative_operation_refs"] = []
            blocked_rules.append(
                {
                    "rule_id": rule_id,
                    "source_id": rule.get("source_id"),
                    "source_locator": rule.get("source_locator"),
                    "statement": rule.get("statement"),
                    "reason": _BLOCKED,
                }
            )

    implementation_gate = _dict(asset.get("implementation_binding_gate"))
    implementation_gate_status = _text(implementation_gate.get("status")) or "NOT_BUILT"
    implementation_gate_present = bool(implementation_gate)
    semantic_ready = bool(base_scenario_gate.get("semantic_understanding_ready", True))
    implementation_ready = bool(base_scenario_gate.get("implementation_binding_ready"))
    base_scenario_ready = bool(base_scenario_gate.get("scenario_planning_allowed"))
    implementation_required = bool(promoted)

    if promoted and not base_scenario_ready:
        if not semantic_ready:
            blocking_status = _UPSTREAM_BLOCKED
        elif not implementation_ready:
            blocking_status = _IMPLEMENTATION_BLOCKED
        else:
            blocking_status = _text(base_scenario_gate.get("status")) or _IMPLEMENTATION_BLOCKED
        for rule in rules:
            rule_id = _text(rule.get("rule_id"))
            if rule_id not in ready_rule_ids:
                continue
            ready_rule_ids.discard(rule_id)
            rule["downstream_binding_status"] = blocking_status
            # Preserve authoritative_operation_refs / operation_refs for Behavior IR.
            # Scenario/probe generation remains gated by ready_rule_ids only.
            blocked_rules.append(
                {
                    "rule_id": rule_id,
                    "source_id": rule.get("source_id"),
                    "source_locator": rule.get("source_locator"),
                    "statement": rule.get("statement"),
                    "reason": blocking_status,
                    "scenario_planning_status": base_scenario_gate.get("status"),
                    "implementation_binding_status": implementation_gate_status,
                    "behavior_ir_operation_refs_preserved": bool(
                        _list(rule.get("authoritative_operation_refs"))
                    ),
                }
            )

    asset["rule_library"] = rules
    asset["relationships"] = relationships
    blocked_rule_ids = {
        _text(row.get("rule_id")) for row in blocked_rules if _text(row.get("rule_id"))
    }

    risks = [
        dict(row)
        for row in _list(asset.get("risk_domains"))
        if isinstance(row, dict)
        and not (
            _text(row.get("derivation")) == "chinese_first_business_comprehension"
            and _text(row.get("source_rule_id")) in blocked_rule_ids
        )
    ]
    risk_ids = {_text(row.get("risk_id")) for row in risks}
    for rule in rules:
        rule_id = _text(rule.get("rule_id"))
        if rule_id not in ready_rule_ids:
            continue
        risk_id = f"risk:{rule_id}"
        if risk_id in risk_ids:
            continue
        risk_type = _text(rule.get("risk_type")) or "business_logic"
        risks.append(
            {
                "risk_id": risk_id,
                "source_rule_id": rule_id,
                "source_id": rule.get("source_id"),
                "risk_type": risk_type,
                "severity": rule.get("severity") or "P1",
                "title": f"中文企业资料规则风险：{_text(rule.get('statement'))}",
                "expected": rule.get("statement"),
                "oracle_family": _api._oracle_family(risk_type),
                "evidence": [rule.get("source_id")],
                "derivation": "chinese_first_business_comprehension",
                "downstream_binding_status": _READY,
            }
        )
        risk_ids.add(risk_id)
    asset["risk_domains"] = _api._dedupe_by_id(risks, "risk_id")

    interface_refs_by_rule: dict[str, set[str]] = {}
    table_refs_by_rule: dict[str, set[str]] = {}
    for edge in relationships:
        if not isinstance(edge, dict) or not _api._relationship_is_authoritative(edge):
            continue
        rule_id = _text(edge.get("from"))
        target = _text(edge.get("to"))
        if not rule_id or not target:
            continue
        if _text(edge.get("relation")) == "rule_to_interface":
            interface_refs_by_rule.setdefault(rule_id, set()).add(target)
        elif _text(edge.get("relation")) == "rule_to_table":
            table_refs_by_rule.setdefault(rule_id, set()).add(target)

    oracles = [
        dict(row)
        for row in _list(asset.get("oracle_library"))
        if isinstance(row, dict)
        and not (
            _text(row.get("derivation")) == "chinese_first_business_comprehension"
            and _text(row.get("rule_id")) in blocked_rule_ids
        )
    ]
    oracle_ids = {_text(row.get("oracle_id")) for row in oracles}
    for rule in rules:
        rule_id = _text(rule.get("rule_id"))
        if rule_id not in ready_rule_ids:
            continue
        oracle_id = f"oracle:{rule_id}"
        if oracle_id in oracle_ids:
            continue
        risk_type = _text(rule.get("risk_type")) or "business_logic"
        oracles.append(
            {
                "oracle_id": oracle_id,
                "rule_id": rule_id,
                "family": _api._oracle_family(risk_type),
                "assertion": rule.get("statement"),
                "linked_interfaces": sorted(interface_refs_by_rule.get(rule_id, set())),
                "linked_tables": sorted(table_refs_by_rule.get(rule_id, set())),
                "execution_policy": "read_only_evidence_or_sandbox",
                "evidence_requirements": [
                    "original_chinese_source_span",
                    "source_document_version",
                    "authoritative_interface_contract",
                    "response_or_data_snapshot",
                ],
                "derivation": "chinese_first_business_comprehension",
            }
        )
        oracle_ids.add(oracle_id)
    asset["oracle_library"] = _api._dedupe_by_id(oracles, "oracle_id")

    probes = _api._probes_from_asset(asset, int(max_probe_count))
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        lineage = _dict(probe.get("knowledge_lineage"))
        rule_id = _text(lineage.get("rule_id"))
        if rule_id in ready_rule_ids:
            lineage["business_comprehension_gate"] = _READY
            lineage["implementation_binding_gate"] = implementation_gate_status
            lineage["scenario_planning_gate"] = base_scenario_gate.get("status")
            lineage["fact_authority"] = "original_chinese_source_span"
            probe["knowledge_lineage"] = lineage

    relationships_without_probe_edges = [
        row
        for row in relationships
        if not (
            _text(row.get("relation")) == "risk_to_probe"
            and _text(row.get("to")).startswith("probe:")
        )
    ]
    for probe in probes:
        lineage = _dict(probe.get("knowledge_lineage"))
        risk_id = _text(lineage.get("risk_id"))
        probe_id = _text(probe.get("probe_id"))
        if risk_id and probe_id:
            relationships_without_probe_edges.append(
                {
                    "edge_id": f"edge:risk-probe:{risk_id}:{probe_id}",
                    "from": risk_id,
                    "to": f"probe:{probe_id}",
                    "relation": "risk_to_probe",
                    "confidence": 1.0,
                    "status": "accepted",
                    "derivation": "knowledge_probe_catalog",
                    "evidence": {
                        "execution_policy": probe.get("execution_policy"),
                        "business_comprehension_gate": lineage.get(
                            "business_comprehension_gate", "NOT_APPLICABLE"
                        ),
                        "implementation_binding_gate": lineage.get(
                            "implementation_binding_gate", "NOT_APPLICABLE"
                        ),
                        "scenario_planning_gate": lineage.get(
                            "scenario_planning_gate", "NOT_APPLICABLE"
                        ),
                    },
                }
            )
    asset["relationships"] = _api._dedupe_by_id(
        relationships_without_probe_edges,
        "edge_id",
    )

    source_gate = _dict(asset.get("enterprise_comprehension_gate"))
    source_ready = bool(source_gate.get("entry_allowed", True))
    downstream_ready = source_ready and base_scenario_ready and not blocked_rules
    if not source_ready:
        downstream_status = _UPSTREAM_BLOCKED
    elif not base_scenario_ready:
        downstream_status = (
            _IMPLEMENTATION_BLOCKED
            if semantic_ready and not implementation_ready
            else _text(base_scenario_gate.get("status")) or _UPSTREAM_BLOCKED
        )
    elif blocked_rules:
        downstream_status = "BLOCKED_BUSINESS_COMPREHENSION_DOWNSTREAM_UNBOUND"
    else:
        downstream_status = "PASS"
    downstream_gate = {
        "schema": DOWNSTREAM_GATE_SCHEMA,
        "status": downstream_status,
        "entry_allowed": downstream_ready,
        "scenario_planning_allowed": downstream_ready,
        "execution_allowed": False,
        "accepted_chinese_rule_count": len(promoted),
        "authoritatively_bound_rule_count": len(ready_rule_ids),
        "behavior_ir_bound_rule_count": len(bound_for_behavior_ir_rule_ids),
        "blocked_rule_count": len(blocked_rules),
        "blocked_rules": blocked_rules,
        "implementation_binding_required": implementation_required,
        "implementation_binding_gate_present": implementation_gate_present,
        "implementation_binding_status": implementation_gate_status,
        "implementation_binding_ready": implementation_ready,
        "base_scenario_planning_status": base_scenario_gate.get("status"),
        "binding_contract": (
            "accepted Chinese facts may create scenario assets only after authoritative "
            "rule-to-interface binding and the generic scenario-planning gate pass"
        ),
        "arbitrary_endpoint_fallback_allowed": False,
        "semantic_understanding_gate_is_not_rewritten": True,
        "upstream_gate_status": _text(source_gate.get("status")) or "UNKNOWN",
    }
    source_gate["downstream"] = downstream_gate
    source_gate["scenario_planning_allowed"] = downstream_ready
    asset["enterprise_comprehension_gate"] = source_gate
    asset["chinese_rule_downstream_gate"] = downstream_gate

    final_scenario_gate = dict(base_scenario_gate)
    final_scenario_gate["chinese_rule_downstream_gate"] = downstream_gate
    final_scenario_gate["chinese_rule_downstream_status"] = downstream_status
    final_scenario_gate["entry_allowed"] = downstream_ready
    final_scenario_gate["scenario_planning_allowed"] = downstream_ready
    final_scenario_gate["execution_allowed"] = False
    if base_scenario_ready and blocked_rules:
        final_scenario_gate["status"] = "BLOCKED_BUSINESS_COMPREHENSION_DOWNSTREAM_UNBOUND"
        final_scenario_gate["blocking_reasons"] = list(
            dict.fromkeys(
                [
                    *_list(final_scenario_gate.get("blocking_reasons")),
                    "CHINESE_RULE_AUTHORITATIVE_INTERFACE_BINDING_INCOMPLETE",
                ]
            )
        )
    asset["scenario_planning_gate"] = final_scenario_gate

    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind"))
        not in {
            "BLOCKED_BUSINESS_COMPREHENSION_DOWNSTREAM_UNBOUND",
            "BLOCKED_IMPLEMENTATION_BINDING_GATE",
        }
    ]
    if blocked_rules:
        gaps.append(
            {
                "kind": (
                    "BLOCKED_IMPLEMENTATION_BINDING_GATE"
                    if not base_scenario_ready and semantic_ready
                    else "BLOCKED_BUSINESS_COMPREHENSION_DOWNSTREAM_UNBOUND"
                ),
                "gap_type": (
                    "business_behavior_implementation_binding_not_ready"
                    if not base_scenario_ready and semantic_ready
                    else "accepted_chinese_rule_missing_authoritative_operation"
                ),
                "source_id": "*",
                "blocked_rules": blocked_rules,
                "scenario_planning_status": final_scenario_gate.get("status"),
                "implementation_binding_status": implementation_gate_status,
                "operator_action": (
                    "resolve the generic scenario-planning gate before generating probes"
                    if not base_scenario_ready
                    else "provide or resolve a source-backed rule-to-interface binding; do not bind the rule to the first or nearest endpoint"
                ),
            }
        )
    asset["coverage_gaps"] = gaps

    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "relationship_count": len(asset["relationships"]),
            "risk_domain_count": len(asset["risk_domains"]),
            "oracle_count": len(asset["oracle_library"]),
            "generated_probe_count": len(probes),
            "chinese_rules_downstream_ready": len(ready_rule_ids),
            "chinese_rules_downstream_blocked": len(blocked_rules),
            "business_comprehension_pipeline_status": downstream_status,
            "business_comprehension_pipeline_ready": downstream_ready,
            "scenario_planning_gate_status": final_scenario_gate.get("status"),
            "scenario_planning_allowed": downstream_ready,
            "implementation_binding_gate_status": implementation_gate_status,
            "implementation_binding_gate_ready": implementation_ready,
        }
    )
    asset["summary"] = summary
    return asset, probes


def _persist(
    asset: dict[str, Any],
    probes: list[dict[str, Any]],
    *,
    project_id: str,
    root: Path,
) -> None:
    from . import _api
    from ._common import _write_json
    from ._utils import _paths

    paths = _paths(project_id, root)
    evidence_bundle = _api._evidence_bundle(asset, probes)
    for key, payload in (
        ("asset", asset),
        ("asset_copy", asset),
        (
            "probe_catalog",
            {
                "phase": asset.get("phase"),
                "asset_id": asset.get("asset_id"),
                "count": len(probes),
                "items": probes,
            },
        ),
        ("evidence_bundle", evidence_bundle),
    ):
        path = paths.get(key)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _write_json(path, payload)
    report = paths.get("report")
    if report:
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(
            _api.render_enterprise_business_knowledge_report(asset),
            encoding="utf-8",
        )
    center_page = paths.get("center_page")
    if center_page:
        Path(center_page).parent.mkdir(parents=True, exist_ok=True)
        Path(center_page).write_text(
            _api.render_enterprise_business_knowledge_center(
                project_id,
                root,
                asset=asset,
            ),
            encoding="utf-8",
        )


def install_chinese_business_downstream_refresh():
    """Wrap the existing builder after Chinese source comprehension is installed."""
    from . import _api
    from ._common import ROOT, _safe_project_id

    current = _api.build_enterprise_business_knowledge_asset
    if getattr(current, "_qualibug_chinese_downstream_refresh", False):
        return current
    original = current

    @wraps(original)
    def wrapped(
        project_id: str = "real_project_demo",
        root: Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_root = root or ROOT
        project = _safe_project_id(project_id)
        resolved_options = options or {}
        asset = original(project, resolved_root, resolved_options)
        enriched, probes = refresh_chinese_business_downstream(
            asset,
            max_probe_count=int(resolved_options.get("probe_limit") or 140),
        )
        _persist(enriched, probes, project_id=project, root=resolved_root)
        return enriched

    wrapped._qualibug_chinese_downstream_refresh = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_builder = original  # type: ignore[attr-defined]
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "DOWNSTREAM_GATE_SCHEMA",
    "refresh_chinese_business_downstream",
    "install_chinese_business_downstream_refresh",
]
