"""Effect Observation Graph for independent business-effect evidence.

Builds a fingerprint-only observation graph from compiled experiment steps and
runtime observer/contract receipts. Prefer independent readback over the write
response itself. This module never invents operations, never executes requests,
and never creates findings.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

SCHEMA_VERSION = "qualibug.effect-observation-graph.v1"
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_INDEPENDENT_OBSERVERS = frozenset({
    "before_state",
    "after_state",
    "final_state",
    "business_effect",
    "entity_state",
    "authorization_comparison",
})
_GATE_STATUSES = frozenset({"COMPLETE", "WRITE_RESPONSE_ONLY", "INCOMPLETE", "NOT_APPLICABLE"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:24]


def _plan_steps(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for key in ("control_plan", "treatment_plan", "setup_plan"):
        for raw in _list(experiment.get(key)):
            row = _dict(raw)
            if row:
                steps.append(row)
    return steps


def _is_write_step(step: dict[str, Any]) -> bool:
    method = _text(step.get("method")).upper()
    if method in _WRITE_METHODS:
        return True
    op = _dict(step.get("operation"))
    return _text(op.get("method")).upper() in _WRITE_METHODS


def _observer_rows(result: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in (
        _list(result.get("observer_receipts")),
        _list(evidence.get("observer_receipts")),
        _list(_dict(evidence.get("observations")).get("observer_receipts")),
    ):
        for raw in source:
            row = _dict(raw)
            if row:
                rows.append(row)
    return rows


def score_observation_surface(surface: dict[str, Any]) -> dict[str, Any]:
    """Score one observation surface without inventing missing evidence."""
    kind = _text(surface.get("kind")).lower()
    observer_id = _text(surface.get("observer_id")).lower()
    path = _text(surface.get("observation_path") or surface.get("path"))
    status = _text(surface.get("status")).upper()
    independent = bool(
        surface.get("independent_of_write_response") is True
        or observer_id in _INDEPENDENT_OBSERVERS
        or (kind == "readback" and path.startswith("/"))
    )
    write_response = bool(
        surface.get("write_response_only") is True
        or kind in {"write_response", "http_response"}
        or observer_id in {"http_response", "response_status"}
    )
    observed = status in {"OBSERVED", "COMPLETED", "ACTIVE"}
    score = 0
    if independent and observed:
        score += 40
    if _text(surface.get("entity_identity_fingerprint")):
        score += 15
    if surface.get("before_observed") is True and surface.get("after_observed") is True:
        score += 20
    if surface.get("source_declared") is True:
        score += 10
    if write_response and not independent:
        score = min(score, 10)
    return {
        "independence": independent,
        "write_response_dependent": write_response and not independent,
        "observed": observed,
        "score": score,
        "factors": {
            "independence": independent,
            "causal_relevance": bool(surface.get("causal_operation_ref")),
            "state_persistence": bool(
                surface.get("before_observed") and surface.get("after_observed")
            ),
            "source_authority": surface.get("source_declared") is True,
            "entity_identity_confidence": bool(
                surface.get("entity_identity_fingerprint")
            ),
        },
    }


def build_effect_observation_graph(
    *,
    experiment: dict[str, Any],
    result: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project causal writes to declared/runtime observation surfaces."""
    exp = _dict(experiment)
    out = _dict(result)
    ev = _dict(evidence)
    if not ev:
        ev = {
            "observer_receipts": _list(out.get("observer_receipts")),
            "contract_evidence_receipts": _list(out.get("contract_evidence_receipts")),
            "observations": _dict(out.get("observations")),
        }

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    write_ops: list[dict[str, Any]] = []
    for index, step in enumerate(_plan_steps(exp)):
        if not _is_write_step(step):
            continue
        op_ref = _text(step.get("operation_ref") or step.get("id") or f"write:{index}")
        node = {
            "node_id": f"causal:{op_ref}:{index}",
            "kind": "causal_operation",
            "operation_ref": op_ref,
            "method": _text(step.get("method")).upper(),
            "path_fingerprint": _fingerprint(_text(step.get("path"))),
            "actor_ref": _text(step.get("actor_ref")),
        }
        nodes.append(node)
        write_ops.append(node)
        obs_path = _text(
            _dict(step.get("governance_receipt")).get("observation_path")
            or step.get("observation_path")
        )
        if obs_path.startswith("/"):
            read_id = f"readback:{_fingerprint(obs_path)}"
            nodes.append(
                {
                    "node_id": read_id,
                    "kind": "readback",
                    "observation_path": obs_path,
                    "path_fingerprint": _fingerprint(obs_path),
                    "independent_of_write_response": True,
                    "source_declared": True,
                    "causal_operation_ref": op_ref,
                }
            )
            edges.append(
                {
                    "from": node["node_id"],
                    "to": read_id,
                    "relation": "independent_readback",
                }
            )

    for raw in _observer_rows(out, ev):
        observer_id = _text(raw.get("observer_id"))
        if not observer_id:
            continue
        evidence_body = _dict(raw.get("evidence"))
        obs_path = _text(
            evidence_body.get("observation_path")
            or raw.get("observation_path")
            or evidence_body.get("path")
        )
        node_id = f"observer:{observer_id}:{_fingerprint(raw.get('receipt_id') or observer_id)}"
        surface = {
            "node_id": node_id,
            "kind": "observer",
            "observer_id": observer_id,
            "status": _text(raw.get("status")).upper(),
            "observation_path": obs_path,
            "path_fingerprint": _fingerprint(obs_path) if obs_path else "",
            "independent_of_write_response": observer_id in _INDEPENDENT_OBSERVERS,
            "write_response_only": observer_id in {"http_response", "response_status"},
            "causal_operation_ref": _text(evidence_body.get("operation_ref")),
            "entity_identity_fingerprint": _text(
                evidence_body.get("entity_identity_fingerprint")
                or evidence_body.get("resource_identity_fingerprint")
            ),
            "before_observed": bool(
                evidence_body.get("before_observed")
                or evidence_body.get("before_fingerprint")
                or (
                    observer_id == "before_state"
                    and _text(raw.get("status")).upper() == "OBSERVED"
                )
            ),
            "after_observed": bool(
                evidence_body.get("after_observed")
                or evidence_body.get("after_fingerprint")
                or (
                    observer_id in {"after_state", "final_state", "business_effect"}
                    and _text(raw.get("status")).upper() == "OBSERVED"
                )
            ),
            "source_declared": bool(evidence_body.get("source_declared")),
        }
        surface["scoring"] = score_observation_surface(surface)
        nodes.append(surface)
        for write in write_ops:
            edges.append(
                {
                    "from": write["node_id"],
                    "to": node_id,
                    "relation": "observed_by",
                }
            )

    for write in write_ops:
        nodes.append(
            {
                "node_id": f"write_response:{write['operation_ref']}",
                "kind": "write_response",
                "observer_id": "http_response",
                "write_response_only": True,
                "independent_of_write_response": False,
                "causal_operation_ref": write["operation_ref"],
                "status": "AVAILABLE",
                "scoring": score_observation_surface(
                    {
                        "kind": "write_response",
                        "observer_id": "http_response",
                        "write_response_only": True,
                        "status": "AVAILABLE",
                    }
                ),
            }
        )

    independent_observed = [
        node
        for node in nodes
        if _dict(node.get("scoring")).get("independence") is True
        and _dict(node.get("scoring")).get("observed") is True
    ]
    readback_nodes = [
        node
        for node in nodes
        if _text(node.get("kind")) == "readback"
        or (
            _text(node.get("kind")) == "observer"
            and _dict(node.get("scoring")).get("independence") is True
        )
    ]
    reason_codes: list[str] = []
    if write_ops and not independent_observed and not readback_nodes:
        status = "WRITE_RESPONSE_ONLY"
        reason_codes.append("WRITE_RESPONSE_ONLY_EVIDENCE")
    elif write_ops and not independent_observed:
        status = "INCOMPLETE"
        reason_codes.append("INDEPENDENT_READBACK_NOT_OBSERVED")
    elif not write_ops:
        status = "NOT_APPLICABLE"
    else:
        status = "COMPLETE"

    body = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": _text(exp.get("experiment_id") or out.get("experiment_id")),
        "obligation_id": _text(exp.get("obligation_id") or out.get("obligation_id")),
        "status": status,
        "reason_codes": sorted(set(reason_codes)),
        "write_operation_count": len(write_ops),
        "independent_readback_count": len(readback_nodes),
        "independent_observed_count": len(independent_observed),
        "nodes": nodes,
        "edges": edges,
        "preferred_evidence_order": [
            "independent_query",
            "related_entity_query",
            "database_readonly",
            "event_or_audit",
            "ui_state",
            "write_response",
        ],
    }
    if status not in _GATE_STATUSES:
        raise ValueError(f"effect_observation_graph_status_invalid:{status}")
    return {
        **body,
        "receipt_id": "eog_" + _fingerprint(body),
        "graph_fingerprint": _fingerprint(
            {
                "nodes": [
                    {
                        "node_id": n.get("node_id"),
                        "kind": n.get("kind"),
                        "observer_id": n.get("observer_id"),
                        "path_fingerprint": n.get("path_fingerprint"),
                    }
                    for n in nodes
                ],
                "edges": edges,
                "status": status,
            }
        ),
    }


def requires_independent_readback(experiment: dict[str, Any]) -> bool:
    """True when the experiment contains a governed write that claims persistence."""
    risk = _text(
        experiment.get("risk_family")
        or _dict(experiment.get("property")).get("risk_family")
    ).lower()
    if risk in {
        "state",
        "conservation",
        "idempotency",
        "concurrency",
        "temporal",
        "validation",
    }:
        return any(_is_write_step(step) for step in _plan_steps(experiment))
    property_row = _dict(experiment.get("property"))
    assertion_kind = _text(
        property_row.get("assertion_kind") or property_row.get("kind")
    ).lower()
    if assertion_kind in {
        "state_transition",
        "entity_state",
        "conservation",
        "field_equals",
        "business_effect",
    }:
        return any(_is_write_step(step) for step in _plan_steps(experiment))
    return False


__all__ = [
    "SCHEMA_VERSION",
    "build_effect_observation_graph",
    "requires_independent_readback",
    "score_observation_surface",
]
