"""Stable public facade for relationship linking and Probe compilation.

The implementation is kept in ``_linking_impl``. This facade owns public boundary
semantics: a non-positive Probe budget produces no Probe, and a Probe may leave the
knowledge layer only when its binding identity is source-backed. When the formal
Scenario -> Runtime Plan -> Runtime Materialization chain exists, that chain is the
only Probe source; the legacy risk-domain compiler cannot select an endpoint.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import _linking_impl as _impl
from ._linking_impl import *  # noqa: F401,F403

__all__ = list(_impl.__all__)

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_AUTHORITATIVE_RELATION_STATUSES = frozenset(
    {"accepted", "active", "confirmed", "verified", "resolved"}
)
_NON_AUTHORITATIVE_RELATION_DERIVATIONS = frozenset(
    {
        "token_overlap",
        "path_segment_heuristic",
        "token_overlap_only_requires_explicit_source_relation",
    }
)


def _relationship_is_authoritative(edge: dict[str, Any]) -> bool:
    """Admit only explicit formal relationships with structured evidence."""
    if not isinstance(edge, dict):
        return False
    status = str(edge.get("status") or "").strip().lower()
    if status not in _AUTHORITATIVE_RELATION_STATUSES:
        return False
    derivation = str(edge.get("derivation") or "").strip().lower().replace("-", "_")
    evidence_gate = str(edge.get("evidence_gate") or "").strip().lower().replace("-", "_")
    if (
        derivation in _NON_AUTHORITATIVE_RELATION_DERIVATIONS
        or evidence_gate in _NON_AUTHORITATIVE_RELATION_DERIVATIONS
    ):
        return False
    evidence = edge.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return False
    if set(evidence) <= {"token_overlap"}:
        return False
    return True


# One shared authority function for legacy linker internals and all facade consumers.
_impl._relationship_is_authoritative = _relationship_is_authoritative
if "_relationship_is_authoritative" not in __all__:
    __all__.append("_relationship_is_authoritative")


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (value or []) if isinstance(row, dict)]


def _stable_probe_id(*parts: Any) -> str:
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return "RP_BINDING_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16].upper()


def _authoritative_probe_pairs(asset: dict[str, Any]) -> set[tuple[str, str]]:
    """Return exact rule/interface identities admitted by existing relationship authority."""
    pairs: set[tuple[str, str]] = set()
    for edge in asset.get("relationships") or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("relation") or "") != "rule_to_interface":
            continue
        if not _impl._relationship_is_authoritative(edge):
            continue
        rule_id = str(edge.get("from") or "").strip()
        interface_id = str(edge.get("to") or "").strip()
        if rule_id and interface_id:
            pairs.add((rule_id, interface_id))
    return pairs


def _runtime_plan_interface_ids(asset: dict[str, Any]) -> set[str] | None:
    """Return governed Runtime Plan interfaces when that formal stage exists.

    ``None`` means the enterprise-understanding Runtime Plan stage is not present and
    therefore cannot add an extra restriction. An empty set means the stage exists but
    is closed or contains no formal authoritative action, so no Probe may pass.
    """
    gate = asset.get("runtime_plan_gate")
    if not isinstance(gate, dict):
        return None
    if not bool(gate.get("entry_allowed")):
        return set()

    result: set[str] = set()
    for plan in asset.get("runtime_plans") or []:
        if not isinstance(plan, dict):
            continue
        if not bool(plan.get("formal_runtime_plan")):
            continue
        action = plan.get("action_entry") if isinstance(plan.get("action_entry"), dict) else {}
        if not bool(action.get("authoritative")):
            continue
        interface_id = str(action.get("interface_id") or "").strip()
        if interface_id:
            result.add(interface_id)
    return result


def _formal_runtime_stage_present(asset: dict[str, Any]) -> bool:
    return any(
        key in asset
        for key in (
            "scenario_ir_gate",
            "scenario_execution_contract_gate",
            "runtime_plan_gate",
            "runtime_materialization_gate",
        )
    )


def _expected_text(scenario: dict[str, Any], plan: dict[str, Any]) -> str:
    expected = _dict(scenario.get("expected_outcome"))
    permission = str(expected.get("permission_decision") or "").strip()
    effects = [
        str(value).strip()
        for value in expected.get("expected_effects") or []
        if str(value).strip()
    ]
    state_effects = [
        str(row.get("statement") or row.get("effect") or row)
        for row in expected.get("state_effects") or []
        if isinstance(row, dict)
    ]
    data_effects = [
        str(row.get("statement") or row.get("effect") or row)
        for row in expected.get("data_effects") or []
        if isinstance(row, dict)
    ]
    parts = [
        value
        for value in [
            f"permission={permission}" if permission else "",
            *effects,
            *state_effects,
            *data_effects,
        ]
        if value
    ]
    if parts:
        return "；".join(parts)
    oracle = _dict(plan.get("oracle_query_templates"))
    return str(oracle.get("oracle_level") or "source-backed runtime oracle must hold")


def _oracle_family(scenario_type: str) -> str:
    normalized = str(scenario_type or "").upper()
    if normalized in {"UNAUTHORIZED", "REJECTION"}:
        return "authorization_boundary_oracle"
    if normalized == "STATE_TRANSITION":
        return "state_transition_oracle"
    if normalized == "BOUNDARY":
        return "parameter_boundary_oracle"
    return "business_rule_oracle"


def _formal_runtime_probes(
    asset: dict[str, Any], limit: int
) -> list[dict[str, Any]] | None:
    """Compile Probes only from formal Runtime Plan + Materialization identities.

    ``None`` signals that the formal enterprise-understanding chain is absent and the
    backwards-compatible legacy compiler may still be used. Once any formal gate is
    present, a closed or incomplete chain produces no Probe rather than a fallback.
    """
    if not _formal_runtime_stage_present(asset):
        return None

    required_gates = (
        "scenario_ir_gate",
        "scenario_execution_contract_gate",
        "runtime_plan_gate",
        "runtime_materialization_gate",
    )
    for key in required_gates:
        gate = _dict(asset.get(key))
        if not gate or not bool(gate.get("entry_allowed")):
            return []

    scenarios = {
        str(row.get("scenario_id") or "").strip(): row
        for row in _rows(asset.get("scenario_ir"))
        if str(row.get("scenario_id") or "").strip()
    }
    materials_by_plan: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(asset.get("runtime_materializations")):
        plan_ref = str(row.get("runtime_plan_ref") or "").strip()
        if not plan_ref:
            continue
        if str(row.get("status") or "") != "DRAFT_READY":
            continue
        if row.get("formal_runtime_materialization") is False:
            continue
        materials_by_plan.setdefault(plan_ref, []).append(row)

    probes: list[dict[str, Any]] = []
    for plan in sorted(
        _rows(asset.get("runtime_plans")),
        key=lambda row: str(row.get("plan_id") or ""),
    ):
        if str(plan.get("status") or "") != "TEMPLATE_READY":
            continue
        if not bool(plan.get("formal_runtime_plan")):
            continue

        plan_id = str(plan.get("plan_id") or "").strip()
        action = _dict(plan.get("action_entry"))
        interface_id = str(action.get("interface_id") or "").strip()
        action_surface_ref = str(
            action.get("action_surface_binding_ref")
            or _dict(plan.get("binding_identity_refs")).get(
                "action_surface_binding_ref"
            )
            or ""
        ).strip()
        if not (
            plan_id
            and interface_id
            and bool(action.get("authoritative"))
            and action_surface_ref
        ):
            continue

        materializations = materials_by_plan.get(plan_id) or []
        if len(materializations) != 1:
            continue
        materialization = materializations[0]
        materialization_id = str(
            materialization.get("materialization_id") or ""
        ).strip()
        materialized_action_ref = str(
            _dict(materialization.get("binding_identity_refs")).get(
                "action_surface_binding_ref"
            )
            or _dict(materialization.get("request_draft")).get(
                "action_surface_binding_ref"
            )
            or ""
        ).strip()
        if not materialization_id or materialized_action_ref != action_surface_ref:
            continue

        scenario_ref = str(plan.get("scenario_ref") or "").strip()
        scenario = scenarios.get(scenario_ref) or {}
        scenario_type = str(
            scenario.get("scenario_type")
            or plan.get("scenario_type")
            or "BUSINESS_RULE"
        ).upper()
        method = str(action.get("method") or "GET").upper()
        path = str(action.get("path") or "/")
        destructive = method in _WRITE_METHODS
        behavior_ref = str(plan.get("behavior_ref") or "").strip()
        implementation_binding_ref = str(
            plan.get("implementation_binding_ref") or ""
        ).strip()
        formal_ui_refs = [
            str(row.get("action_surface_binding_id") or "")
            for row in _rows(scenario.get("formal_ui_surface_bindings"))
            if str(row.get("action_surface_binding_id") or "")
        ]
        title = str(
            scenario.get("title")
            or f"{method} {path} — {scenario_type}"
        )
        expected = _expected_text(scenario, plan)
        probe_id = _stable_probe_id(
            plan_id,
            materialization_id,
            action_surface_ref,
            scenario_ref,
        )
        probes.append(
            {
                "probe_id": probe_id,
                "source": "enterprise_understanding_runtime_plan",
                "knowledge_asset_id": asset.get("asset_id"),
                "risk_type": f"enterprise_behavior_{scenario_type.lower()}",
                "knowledge_risk_type": scenario_type.lower(),
                "severity": scenario.get("severity") or "P1",
                "title": title,
                "method": method,
                "path": path,
                "operation_id": action.get("operation_id") or "",
                "actor": (
                    (scenario.get("actor_refs") or ["normal_user"])[0]
                    if isinstance(scenario.get("actor_refs"), list)
                    else "normal_user"
                ),
                "expected": expected,
                "bug_signal": (
                    "运行时结果与经过治理的业务行为、字段、Observer 或 UI 合同不一致。"
                ),
                "oracle_family": _oracle_family(scenario_type),
                "oracle_assertion": expected,
                "destructive": destructive,
                "execution_policy": (
                    "sandbox_required" if destructive else "candidate_only"
                ),
                "runtime_plan_ref": plan_id,
                "runtime_materialization_ref": materialization_id,
                "scenario_ref": scenario_ref,
                "behavior_ref": behavior_ref,
                "implementation_binding_ref": implementation_binding_ref,
                "action_surface_binding_ref": action_surface_ref,
                "formal_ui_surface_binding_refs": formal_ui_refs,
                "knowledge_lineage": {
                    "risk_id": "",
                    # Compatibility field only; endpoint authority comes from the
                    # governed behavior/runtime identities below.
                    "rule_id": behavior_ref,
                    "behavior_ref": behavior_ref,
                    "scenario_ref": scenario_ref,
                    "implementation_binding_ref": implementation_binding_ref,
                    "runtime_plan_ref": plan_id,
                    "runtime_materialization_ref": materialization_id,
                    "interface_id": interface_id,
                    "action_surface_binding_ref": action_surface_ref,
                    "contract_field_binding_refs": list(
                        _dict(plan.get("binding_identity_refs")).get(
                            "contract_field_binding_refs"
                        )
                        or []
                    ),
                    "runtime_value_binding_refs": list(
                        _dict(plan.get("binding_identity_refs")).get(
                            "runtime_value_binding_refs"
                        )
                        or []
                    ),
                    "binding_authority": (
                        "formal_runtime_plan_and_materialization_identity"
                    ),
                    "legacy_risk_domain_endpoint_selection_used": False,
                    "arbitrary_endpoint_fallback_used": False,
                    "token_overlap_is_authoritative": False,
                },
                "evidence_requirements": [
                    "enterprise_knowledge_asset",
                    "business_behavior_ir",
                    "implementation_binding_identity_graph",
                    "scenario_execution_contract",
                    "runtime_plan",
                    "runtime_materialization",
                    "runtime_evidence_or_sandbox_replay",
                ],
            }
        )
        if len(probes) >= limit:
            break
    return probes


def _legacy_governed_probes(
    asset: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    """Preserve the old compiler only for assets that predate the formal chain."""
    authoritative_pairs = _authoritative_probe_pairs(asset)
    runtime_interface_ids = _runtime_plan_interface_ids(asset)
    admitted: list[dict[str, Any]] = []
    for raw in _impl._probes_from_asset(asset, limit):
        if not isinstance(raw, dict):
            continue
        probe = dict(raw)
        lineage = (
            dict(probe.get("knowledge_lineage"))
            if isinstance(probe.get("knowledge_lineage"), dict)
            else {}
        )
        rule_id = str(lineage.get("rule_id") or "").strip()
        interface_id = str(lineage.get("interface_id") or "").strip()
        if (rule_id, interface_id) not in authoritative_pairs:
            continue
        if runtime_interface_ids is not None and interface_id not in runtime_interface_ids:
            continue

        lineage["binding_authority"] = "accepted_rule_to_interface_relationship"
        lineage["legacy_risk_domain_endpoint_selection_used"] = True
        lineage["arbitrary_endpoint_fallback_used"] = False
        lineage["token_overlap_is_authoritative"] = False
        if runtime_interface_ids is not None:
            lineage["runtime_plan_interface_admitted"] = True
        probe["knowledge_lineage"] = lineage
        admitted.append(probe)
        if len(admitted) >= limit:
            break
    return admitted


def _probes_from_asset(
    asset: dict[str, Any], max_count: int = 140
) -> list[dict[str, Any]]:
    """Compile at most ``max_count`` Probes from the strongest available authority."""
    limit = int(max_count)
    if limit <= 0:
        return []

    formal = _formal_runtime_probes(asset, limit)
    if formal is not None:
        return formal
    return _legacy_governed_probes(asset, limit)


def __getattr__(name: str) -> Any:
    """Preserve direct private-symbol compatibility during the module split."""
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
