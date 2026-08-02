"""Project experimentability receipts for accepted business facts.

After Business World Model identity closure and before scenario planning, every
ACCEPTED fact receives exactly one FactExperimentabilityReceipt. Receipts are
reference-only: they cite existing Canonical IDs and never copy a second
Business World Model payload.

Schema:
  qualibug.fact-experimentability-receipt.v1
  qualibug.fact-experimentability-ledger.v1
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

RECEIPT_SCHEMA = "qualibug.fact-experimentability-receipt.v1"
LEDGER_SCHEMA = "qualibug.fact-experimentability-ledger.v1"

EXPERIMENTABILITY_STATUSES = frozenset(
    {
        "READY",
        "NOT_TEST_WORTHY",
        "MISSING_PRIMARY_OPERATION",
        "AMBIGUOUS_OPERATION",
        "MISSING_ACTOR",
        "MISSING_CREDENTIAL",
        "MISSING_PRECONDITION",
        "MISSING_BINDING",
        "MISSING_FIXTURE",
        "MISSING_OBSERVER",
        "MISSING_CLEANUP",
        "NON_REVERSIBLE_WRITE",
        "UNSAFE_OPERATION",
        "INSUFFICIENT_SOURCE_AUTHORITY",
        "CONFLICTED_FACT",
    }
)

_TEST_WORTHY_KINDS = frozenset(
    {
        "RULE",
        "STATE_TRANSITION",
        "BUSINESS_RULE",
        "AUTHORIZATION",
        "PERMISSION",
        "INVARIANT",
        "CONSTRAINT",
        "LIFECYCLE",
        "VISIBILITY",
        "ISOLATION",
        "CONCURRENCY",
        "IDEMPOTENCY",
        "TEMPORAL",
        "APPROVAL",
        "CARDINALITY_CONSTRAINT",
    }
)

_DESCRIPTIVE_KINDS = frozenset(
    {
        "TERM",
        "TERM_ALIAS",
        "GLOSSARY",
        "DESCRIPTION",
        "NARRATIVE",
        "NOTE",
        "DEFINITION",
    }
)

_WRITE_METHOD_HINTS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_CLEANUP_VERB_HINTS = frozenset(
    {
        "delete",
        "remove",
        "cancel",
        "revert",
        "rollback",
        "compensate",
        "restore",
        "undo",
        "void",
        "revoke",
        "禁用",
        "删除",
        "取消",
        "回滚",
        "撤销",
        "恢复",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in _list(values):
        item = _text(value)
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_receipt_id(fact_id: str, risk_operator: str) -> str:
    material = "\x1f".join([RECEIPT_SCHEMA, fact_id, risk_operator])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"fer_{digest}"


def _ledger_fingerprint(items: list[dict[str, Any]]) -> str:
    payload = [
        {
            "receipt_id": row.get("receipt_id"),
            "fact_ref": row.get("fact_ref"),
            "status": row.get("status"),
            "risk_operator": row.get("risk_operator"),
            "blocker_codes": row.get("blocker_codes"),
        }
        for row in items
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _fact_refs_from_source(value: Any) -> list[str]:
    refs: list[str] = []
    for item in _list(value):
        if isinstance(item, str):
            text = _text(item)
            if text.startswith("fact") or text.startswith("fact:"):
                refs.append(text)
            continue
        if isinstance(item, dict):
            for key in ("fact_id", "fact_ref", "ref", "id"):
                text = _text(item.get(key))
                if text.startswith("fact") or text.startswith("fact:"):
                    refs.append(text)
                    break
            locator = _text(item.get("locator") or item.get("kind"))
            if locator.startswith("fact") or locator.startswith("fact:"):
                refs.append(locator)
    return _unique(refs)


def _object_refs_from_fact(fact: dict[str, Any]) -> list[str]:
    subject = _dict(fact.get("subject"))
    obj = _dict(fact.get("object"))
    return _unique(
        [
            *(_list(subject.get("entity_refs"))),
            *(_list(obj.get("entity_refs"))),
            *(_list(fact.get("object_refs"))),
            *(_list(fact.get("entity_refs"))),
        ]
    )


def _actor_refs_from_fact(fact: dict[str, Any]) -> list[str]:
    subject = _dict(fact.get("subject"))
    return _unique(
        [
            *(_list(subject.get("actor_refs"))),
            *(_list(fact.get("actor_refs"))),
            *(_list(_dict(fact.get("scope")).get("actor_refs"))),
        ]
    )


def _state_refs_from_fact(fact: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("state_effects", "state_preconditions", "preconditions", "conditions"):
        for row in _list(fact.get(key)):
            if isinstance(row, dict):
                for field in ("state", "to_state", "from_state", "state_ref", "field_candidate"):
                    value = _text(row.get(field))
                    if value:
                        refs.append(value)
            else:
                value = _text(row)
                if value:
                    refs.append(value)
    return _unique(refs)


def _source_refs_from_fact(fact: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for span in _list(fact.get("source_spans")):
        if not isinstance(span, dict):
            continue
        source_id = _text(span.get("source_id"))
        locator = _text(span.get("locator") or span.get("source_locator"))
        if source_id and locator:
            refs.append(f"{source_id}#{locator}")
        elif source_id:
            refs.append(source_id)
        elif locator:
            refs.append(locator)
    for item in _list(fact.get("source_refs")):
        if isinstance(item, str) and _text(item):
            refs.append(_text(item))
        elif isinstance(item, dict):
            source_id = _text(item.get("source_id") or item.get("ref"))
            if source_id:
                refs.append(source_id)
    return _unique(refs)


def _infer_risk_operator(fact: dict[str, Any]) -> str:
    kind = _text(fact.get("fact_type") or fact.get("kind")).upper()
    modality = _text(fact.get("modality")).upper()
    auth = _dict(fact.get("authorization") if isinstance(fact.get("authorization"), dict) else {})
    if auth or kind in {"AUTHORIZATION", "PERMISSION", "VISIBILITY", "ISOLATION"}:
        if kind in {"ISOLATION"}:
            return "isolation_boundary"
        if kind in {"VISIBILITY"}:
            return "visibility_control"
        return "authorization_bypass"
    if kind in {"STATE_TRANSITION", "LIFECYCLE"} or _list(fact.get("state_effects")):
        return "illegal_state_transition"
    if kind in {"CONCURRENCY"}:
        return "concurrency_race"
    if kind in {"IDEMPOTENCY"}:
        return "idempotency_violation"
    if kind in {"TEMPORAL", "APPROVAL"}:
        return "temporal_window_violation"
    if kind in {"CARDINALITY_CONSTRAINT", "INVARIANT", "CONSTRAINT"}:
        return "invariant_violation"
    if modality in {"FORBIDDEN", "MUST_NOT", "PROHIBITED"}:
        return "forbidden_effect"
    if _list(fact.get("data_effects")):
        return "data_effect_inconsistency"
    if kind in _TEST_WORTHY_KINDS or bool(fact.get("critical")):
        return "business_rule_violation"
    return "not_test_worthy"


def _is_test_worthy(fact: dict[str, Any]) -> bool:
    kind = _text(fact.get("fact_type") or fact.get("kind")).upper()
    if kind in _DESCRIPTIVE_KINDS:
        return False
    if bool(fact.get("critical")):
        return True
    if kind in _TEST_WORTHY_KINDS:
        return True
    modality = _text(fact.get("modality")).upper()
    if modality in {"MUST", "MUST_NOT", "FORBIDDEN", "REQUIRED", "ASSERTS", "SHALL"}:
        return True
    if _list(fact.get("data_effects")) or _list(fact.get("state_effects")):
        return True
    if _dict(fact.get("authorization")):
        return True
    return False


def _index_behaviors_by_fact(model: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for behavior in _list(model.get("business_behaviors")):
        if not isinstance(behavior, dict):
            continue
        for fact_ref in _fact_refs_from_source(behavior.get("source_refs")):
            index.setdefault(fact_ref, []).append(behavior)
        evidence_fact_ids = [
            _text(row.get("fact_id"))
            for row in _list(behavior.get("evidence"))
            if isinstance(row, dict) and _text(row.get("fact_id"))
        ]
        for fact_ref in evidence_fact_ids:
            index.setdefault(fact_ref, []).append(behavior)
    return index


def _index_bindings_by_behavior(model: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for binding in _list(model.get("behavior_implementation_bindings")):
        if not isinstance(binding, dict):
            continue
        behavior_ref = _text(
            binding.get("behavior_ref") or binding.get("source_behavior_ref")
        )
        if behavior_ref:
            index.setdefault(behavior_ref, []).append(binding)
    return index


def _operation_method(operation: dict[str, Any]) -> str:
    return _text(
        operation.get("method")
        or operation.get("http_method")
        or operation.get("verb")
    ).upper()


def _index_operations(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("operation_id")): row
        for row in _list(model.get("operations"))
        if isinstance(row, dict) and _text(row.get("operation_id"))
    }


def _binding_operation_refs(bindings: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    for binding in bindings:
        for key in (
            "operation_ref",
            "primary_operation_ref",
            "causal_operation_ref",
            "endpoint_operation_ref",
            "implemented_operation_ref",
        ):
            value = _text(binding.get(key))
            if value:
                refs.append(value)
        endpoint = _dict(binding.get("endpoint") or binding.get("operation"))
        for key in ("operation_ref", "operation_id", "id"):
            value = _text(endpoint.get(key))
            if value:
                refs.append(value)
        for row in _list(binding.get("operation_refs")):
            if isinstance(row, str) and _text(row):
                refs.append(_text(row))
            elif isinstance(row, dict):
                value = _text(row.get("operation_ref") or row.get("operation_id"))
                if value:
                    refs.append(value)
    return _unique(refs)


def _implemented_observer_ids() -> list[str]:
    try:
        from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
    except Exception:
        return []
    return sorted(
        observer_id
        for observer_id, contract in OBSERVER_REGISTRY.items()
        if isinstance(contract, dict) and contract.get("implemented") is True
    )


def _observer_refs_for_risk(risk_operator: str, implies_write: bool) -> list[str]:
    implemented = set(_implemented_observer_ids())
    preferred: list[str] = []
    if risk_operator in {
        "authorization_bypass",
        "visibility_control",
        "isolation_boundary",
    }:
        preferred.extend(
            ["actor_identity", "authorization_comparison", "resource_ownership", "http_response"]
        )
    elif risk_operator in {"illegal_state_transition"}:
        preferred.extend(["before_state", "after_state", "entity_state", "final_state"])
    elif risk_operator in {"concurrency_race"}:
        preferred.extend(["barrier_timeline", "http_response"])
    elif risk_operator in {"temporal_window_violation"}:
        preferred.extend(["temporal_window", "after_state", "http_response"])
    elif implies_write or risk_operator in {
        "data_effect_inconsistency",
        "invariant_violation",
        "forbidden_effect",
        "business_rule_violation",
        "idempotency_violation",
    }:
        preferred.extend(
            ["business_effect", "entity_state", "after_state", "source_invariant", "http_response"]
        )
    else:
        preferred.extend(["http_response", "typed_assertion"])
    return [item for item in preferred if item in implemented]


def _fact_implies_write(
    fact: dict[str, Any],
    *,
    operation_refs: list[str],
    operations_by_id: dict[str, dict[str, Any]],
) -> bool:
    if _list(fact.get("data_effects")) or _list(fact.get("state_effects")):
        return True
    if _list(fact.get("compensations")) or _list(fact.get("compensation")):
        return True
    for op_ref in operation_refs:
        method = _operation_method(_dict(operations_by_id.get(op_ref)))
        if method in _WRITE_METHOD_HINTS:
            return True
    return False


def _has_cleanup_capability(
    fact: dict[str, Any],
    *,
    object_refs: list[str],
    operations_by_id: dict[str, dict[str, Any]],
) -> bool:
    if _list(fact.get("compensations")) or _list(fact.get("compensation")):
        return True
    object_set = set(object_refs)
    for operation in operations_by_id.values():
        op_objects = set(
            _unique(
                [
                    *(_list(operation.get("object_refs"))),
                    *(_list(operation.get("entity_refs"))),
                    *(_list(_dict(operation.get("object")).get("entity_refs"))),
                ]
            )
        )
        if object_set and not (object_set & op_objects):
            continue
        label = " ".join(
            [
                _text(operation.get("name")),
                _text(operation.get("label")),
                _text(operation.get("canonical")),
                _text(operation.get("raw")),
                _text(operation.get("path")),
                _operation_method(operation).lower(),
            ]
        ).lower()
        if _operation_method(operation) == "DELETE":
            return True
        if any(token in label for token in _CLEANUP_VERB_HINTS):
            return True
    return False


def _risk_level(fact: dict[str, Any], *, test_worthy: bool) -> str:
    if not test_worthy:
        return "none"
    if bool(fact.get("critical")):
        return "high"
    modality = _text(fact.get("modality")).upper()
    if modality in {"MUST", "MUST_NOT", "FORBIDDEN", "REQUIRED", "SHALL"}:
        return "high"
    kind = _text(fact.get("fact_type") or fact.get("kind")).upper()
    if kind in {"AUTHORIZATION", "PERMISSION", "ISOLATION", "INVARIANT", "CONCURRENCY"}:
        return "high"
    return "medium"


def _build_receipt_for_fact(
    fact: dict[str, Any],
    *,
    behaviors_by_fact: dict[str, list[dict[str, Any]]],
    bindings_by_behavior: dict[str, list[dict[str, Any]]],
    operations_by_id: dict[str, dict[str, Any]],
    conflicted_fact_ids: set[str],
) -> dict[str, Any]:
    fact_id = _text(fact.get("fact_id"))
    risk_operator = _infer_risk_operator(fact)
    test_worthy = _is_test_worthy(fact)
    object_refs = _object_refs_from_fact(fact)
    actor_refs = _actor_refs_from_fact(fact)
    state_refs = _state_refs_from_fact(fact)
    source_refs = _source_refs_from_fact(fact)
    behaviors = list(behaviors_by_fact.get(fact_id, []))
    for behavior in behaviors:
        actor_refs = _unique([*actor_refs, *(_list(behavior.get("actor_refs")))])
        state_refs = _unique(
            [
                *state_refs,
                *[
                    _text(slot.get("state") or slot.get("field_candidate"))
                    for slot in _list(behavior.get("state_preconditions"))
                    if isinstance(slot, dict)
                ],
            ]
        )

    semantic_operation_refs = _unique(
        [_text(row.get("operation_ref")) for row in behaviors if _text(row.get("operation_ref"))]
    )
    bindings: list[dict[str, Any]] = []
    for behavior in behaviors:
        behavior_id = _text(behavior.get("behavior_id"))
        bindings.extend(bindings_by_behavior.get(behavior_id, []))
        for binding_ref in _list(behavior.get("implementation_binding_refs")):
            # binding refs already collected via behavior_id index; keep refs for reporting
            _ = binding_ref
    causal_operation_refs = _binding_operation_refs(bindings)
    # Causal primary ops require an implementation binding. Semantic operation
    # refs alone are candidates, never silent primary bindings.
    if causal_operation_refs:
        required_operation_refs = causal_operation_refs
        candidate_operation_refs = _unique([*causal_operation_refs, *semantic_operation_refs])
    else:
        required_operation_refs = []
        candidate_operation_refs = semantic_operation_refs

    implies_write = _fact_implies_write(
        fact,
        operation_refs=_unique([*required_operation_refs, *candidate_operation_refs]),
        operations_by_id=operations_by_id,
    )
    observer_refs = _observer_refs_for_risk(risk_operator, implies_write)
    cleanup_present = _has_cleanup_capability(
        fact,
        object_refs=object_refs,
        operations_by_id=operations_by_id,
    )

    blocker_codes: list[str] = []
    status = "READY"

    if fact_id in conflicted_fact_ids or _text(fact.get("status")).upper() == "CONFLICTED":
        status = "CONFLICTED_FACT"
        blocker_codes.append("CONFLICTED_FACT")
    elif not test_worthy:
        status = "NOT_TEST_WORTHY"
        risk_operator = "not_test_worthy"
    elif not source_refs and not _list(fact.get("source_spans")):
        status = "INSUFFICIENT_SOURCE_AUTHORITY"
        blocker_codes.append("INSUFFICIENT_SOURCE_AUTHORITY")
    elif not required_operation_refs and not candidate_operation_refs:
        status = "MISSING_PRIMARY_OPERATION"
        blocker_codes.append("MISSING_PRIMARY_OPERATION")
    elif not required_operation_refs and len(candidate_operation_refs) > 1:
        status = "AMBIGUOUS_OPERATION"
        blocker_codes.append("AMBIGUOUS_OPERATION")
    elif not required_operation_refs and candidate_operation_refs:
        # Semantic candidates without a causal implementation binding.
        status = "MISSING_BINDING"
        blocker_codes.append("MISSING_BINDING")
    elif not actor_refs and risk_operator in {
        "authorization_bypass",
        "visibility_control",
        "isolation_boundary",
    }:
        status = "MISSING_ACTOR"
        blocker_codes.append("MISSING_ACTOR")
    elif state_refs and risk_operator == "illegal_state_transition" and not behaviors:
        status = "MISSING_PRECONDITION"
        blocker_codes.append("MISSING_PRECONDITION")
    elif not observer_refs:
        status = "MISSING_OBSERVER"
        blocker_codes.append("MISSING_OBSERVER")
    elif implies_write and not cleanup_present:
        status = "NON_REVERSIBLE_WRITE"
        blocker_codes.append("NON_REVERSIBLE_WRITE")
        blocker_codes.append("MISSING_CLEANUP")
    elif implies_write and not object_refs:
        status = "MISSING_FIXTURE"
        blocker_codes.append("MISSING_FIXTURE")

    if status not in EXPERIMENTABILITY_STATUSES:
        status = "INSUFFICIENT_SOURCE_AUTHORITY"
        blocker_codes.append("UNKNOWN_STATUS_COERCED")

    created_at = _now_iso()
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": _stable_receipt_id(fact_id, risk_operator),
        "campaign_id": "",
        "fact_ref": fact_id,
        "fact_kind": _text(fact.get("fact_type") or fact.get("kind")),
        "source_refs": source_refs,
        "object_refs": object_refs,
        "actor_refs": actor_refs,
        "state_refs": state_refs,
        "risk_operator": risk_operator,
        "risk_level": _risk_level(fact, test_worthy=test_worthy),
        "control_definition": {
            "arm": "control",
            "actor_refs": actor_refs[:1],
            "operation_refs": required_operation_refs[:1],
        },
        "treatment_definition": {
            "arm": "treatment",
            "actor_refs": actor_refs[1:2] or actor_refs[:1],
            "operation_refs": required_operation_refs[:1],
            "risk_operator": risk_operator,
        },
        "preconditions": [
            {"state_ref": state_ref} for state_ref in state_refs
        ],
        "required_operation_refs": required_operation_refs,
        "candidate_operation_refs": candidate_operation_refs,
        "request_binding_refs": _unique(
            [_text(row.get("binding_id")) for row in bindings if _text(row.get("binding_id"))]
        ),
        "observer_refs": observer_refs,
        "fixture_requirements": [
            {
                "object_ref": object_ref,
                "initial_state": state_refs[0] if state_refs else "",
            }
            for object_ref in object_refs
        ],
        "cleanup_requirements": (
            [{"authority_required": True, "object_refs": object_refs}]
            if implies_write
            else []
        ),
        "expected_effect": {
            "risk_operator": risk_operator,
            "object_refs": object_refs,
        },
        "disprover": {
            "risk_operator": risk_operator,
            "description_ref": "expected_business_invariant_violated",
        },
        "required_evidence": observer_refs,
        "status": status,
        "blocker_codes": _unique(blocker_codes),
        "first_loss_stage": None,
        "created_at": created_at,
        "updated_at": created_at,
        "test_worthy": bool(test_worthy),
        "implies_write": bool(implies_write),
        "cleanup_capability_present": bool(cleanup_present),
        "behavior_refs": _unique(
            [_text(row.get("behavior_id")) for row in behaviors if _text(row.get("behavior_id"))]
        ),
    }
    return receipt


def project_fact_experimentability(
    asset: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    """Emit one experimentability receipt per ACCEPTED fact onto the asset/model."""

    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [row for row in _list(ledger.get("items")) if isinstance(row, dict)]
    accepted = [
        row for row in facts if _text(row.get("status")).upper() == "ACCEPTED" and _text(row.get("fact_id"))
    ]
    conflicted_fact_ids = {
        _text(row.get("fact_id"))
        for row in facts
        if _text(row.get("status")).upper() == "CONFLICTED" and _text(row.get("fact_id"))
    }
    # Also treat typed conflict receipts if present.
    for row in _list(asset.get("typed_fact_conflicts") or model.get("typed_fact_conflicts")):
        if isinstance(row, dict):
            for key in ("fact_id", "fact_ref", "left_fact_ref", "right_fact_ref"):
                value = _text(row.get(key))
                if value:
                    conflicted_fact_ids.add(value)

    behaviors_by_fact = _index_behaviors_by_fact(model)
    bindings_by_behavior = _index_bindings_by_behavior(model)
    operations_by_id = _index_operations(model)

    receipts: list[dict[str, Any]] = []
    for fact in sorted(accepted, key=lambda row: _text(row.get("fact_id"))):
        receipts.append(
            _build_receipt_for_fact(
                fact,
                behaviors_by_fact=behaviors_by_fact,
                bindings_by_behavior=bindings_by_behavior,
                operations_by_id=operations_by_id,
                conflicted_fact_ids=conflicted_fact_ids,
            )
        )

    status_counts = dict(Counter(_text(row.get("status")) for row in receipts))
    high_risk = [
        row
        for row in receipts
        if _text(row.get("risk_level")) == "high" and _text(row.get("status")) != "NOT_TEST_WORTHY"
    ]
    ready_count = status_counts.get("READY", 0)
    fingerprint = _ledger_fingerprint(receipts)
    aggregate = {
        "schema_version": LEDGER_SCHEMA,
        "status": "PASS" if len(receipts) == len(accepted) else "BLOCKED_COVERAGE_GAP",
        "accepted_fact_count": len(accepted),
        "receipt_count": len(receipts),
        "high_risk_fact_count": len(high_risk),
        "high_risk_receipt_count": len(high_risk),
        "ready_count": ready_count,
        "blocked_count": sum(
            1 for row in receipts if _text(row.get("status")) not in {"READY", "NOT_TEST_WORTHY"}
        ),
        "not_test_worthy_count": status_counts.get("NOT_TEST_WORTHY", 0),
        "status_counts": status_counts,
        "silent_drop_count": max(0, len(accepted) - len(receipts)),
        "ledger_fingerprint": fingerprint,
        "items": receipts,
        "reference_only": True,
        "semantic_payload_duplication_allowed": False,
        "parallel_pipeline_created": False,
        "created_at": _now_iso(),
    }

    asset["fact_experimentability_ledger"] = aggregate
    model["fact_experimentability_ledger"] = aggregate

    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "fact_experimentability_receipt_count": len(receipts),
            "fact_experimentability_ready_count": ready_count,
            "fact_experimentability_blocked_count": aggregate["blocked_count"],
            "fact_experimentability_high_risk_count": len(high_risk),
            "fact_experimentability_ledger_fingerprint": fingerprint,
            "fact_experimentability_silent_drop_count": aggregate["silent_drop_count"],
        }
    )
    asset["summary"] = summary

    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "fact_experimentability_projection_enabled": True,
            "fact_experimentability_requires_receipt_for_accepted_facts": True,
            "fact_experimentability_silent_drop_allowed": False,
            "fact_experimentability_copies_business_world_model": False,
            "fact_experimentability_uses_fuzzy_operation_name_binding": False,
            "fact_experimentability_blocks_scenario_planning": False,
        }
    )
    asset["governance"] = governance
    return model


__all__ = [
    "RECEIPT_SCHEMA",
    "LEDGER_SCHEMA",
    "EXPERIMENTABILITY_STATUSES",
    "project_fact_experimentability",
]
