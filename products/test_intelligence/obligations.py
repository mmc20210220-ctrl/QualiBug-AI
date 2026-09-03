from __future__ import annotations

"""Pure Test Obligation projection over existing enterprise-understanding truth.

The product consumes the already-built ``enterprise_understanding_model``. It
does not parse documents, resolve authorization, rebuild lifecycle truth, or
create executable experiments. Projection IDs are stable presentation IDs, not
persisted canonical finding/test identities.
"""

import hashlib
import json
from typing import Any

OBLIGATION_SCHEMA = "qualibug.test-obligation.v1"
PROJECTION_SCHEMA = "qualibug.test-obligation-projection.v1"

_IMPLEMENTED_KINDS = (
    "business_rule",
    "lifecycle_transition",
    "authorization",
    "side_effect",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({_text(item) for item in value if _text(item)})


def _projection_id(*parts: Any) -> str:
    material = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"test-obligation:{digest}"


def _normalize_evidence(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in _rows(value):
        source_id = _text(raw.get("source_id"))
        locator = _text(
            raw.get("source_locator")
            or raw.get("locator")
            or raw.get("asset_ref")
            or raw.get("document_block_id")
            or raw.get("document_node_id")
        )
        quote = _text(raw.get("quote"))
        quote_hash = _text(raw.get("quote_hash"))
        if not (source_id and locator and (quote or quote_hash)):
            continue
        row = {
            key: item
            for key, item in {
                "source_id": source_id,
                "source_locator": _text(raw.get("source_locator") or raw.get("locator")),
                "asset_ref": _text(raw.get("asset_ref")),
                "document_block_id": _text(raw.get("document_block_id")),
                "document_node_id": _text(raw.get("document_node_id")),
                "quote": quote,
                "quote_hash": quote_hash,
                "fact_id": _text(raw.get("fact_id")),
                "derivation": _text(raw.get("derivation")),
            }.items()
            if item
        }
        key = (
            source_id,
            locator,
            quote_hash,
            quote,
            _text(row.get("fact_id")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _source_ids(evidence: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            _text(item.get("source_id"))
            for item in evidence
            if _text(item.get("source_id"))
        }
    )


def _copy_preconditions(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    copied: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            copied.append(dict(item))
        elif _text(item):
            copied.append(_text(item))
    return copied


def _base_obligation(
    *,
    source_unit_id: str,
    kind: str,
    source_behavior_id: str = "",
    source_transition_id: str = "",
    title: str,
    objective: str,
    operation_ref: str,
    actor_refs: list[str],
    object_refs: list[str],
    preconditions: list[Any],
    expected_outcomes: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    source_refs: list[str],
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": OBLIGATION_SCHEMA,
        "obligation_id": _projection_id(source_unit_id, kind),
        "obligation_kind": kind,
        "source_unit_id": source_unit_id,
        "source_behavior_id": source_behavior_id,
        "source_transition_id": source_transition_id,
        "title": title,
        "objective": objective,
        "actor_refs": actor_refs,
        "object_refs": object_refs,
        "operation_ref": operation_ref,
        "preconditions": preconditions,
        "expected_outcomes": expected_outcomes,
        "business_constraints": list(constraints or []),
        "source_refs": source_refs,
        "source_ids": _source_ids(evidence),
        "evidence": evidence,
        "derived_from": [
            item
            for item in (
                {"kind": "business_behavior", "id": source_behavior_id}
                if source_behavior_id
                else None,
                {"kind": "lifecycle_transition", "id": source_transition_id}
                if source_transition_id
                else None,
            )
            if item is not None
        ],
        "requirement_finding_ids": [],
        "design_status": "OBLIGATION_ONLY",
        "verification_status": "NOT_MEASURED",
        "runtime_linkage": "NOT_EVALUATED",
        "risk_level": "NOT_ASSESSED",
    }


def _behavior_is_formal(behavior: dict[str, Any]) -> bool:
    return (
        _text(behavior.get("status")).upper() == "CONFIRMED"
        and behavior.get("candidate_only") is not True
        and behavior.get("formal_business_rule") is True
    )


def _behavior_source_units(behavior: dict[str, Any]) -> list[dict[str, Any]]:
    behavior_id = _text(behavior.get("behavior_id"))
    operation = _text(behavior.get("operation_ref"))
    objects = _strings(behavior.get("object_refs"))
    if not behavior_id or not operation or not objects or not _behavior_is_formal(behavior):
        return []

    units: list[dict[str, Any]] = []
    auth_explicit = behavior.get("authorization_semantics_explicit") is True
    auth_kind = _text(behavior.get("authorization_semantic_kind")).upper()
    auth_status = _text(behavior.get("authorization_semantics_status")).upper()
    permission = _text(behavior.get("permission_decision")).upper()
    if (
        auth_explicit
        and auth_kind == "AUTHORIZATION"
        and auth_status == "RESOLVED"
        and permission in {"ALLOW", "DENY"}
    ):
        units.append(
            {
                "source_unit_id": f"behavior:{behavior_id}:authorization",
                "kind": "authorization",
            }
        )

    expected_effects = _strings(behavior.get("expected_effects"))
    data_effects = [
        dict(item)
        for item in _rows(behavior.get("data_effects"))
        if _text(item.get("statement") or item.get("raw"))
    ]
    compensations = _strings(behavior.get("compensations"))
    if expected_effects or data_effects or compensations:
        units.append(
            {
                "source_unit_id": f"behavior:{behavior_id}:side-effect",
                "kind": "side_effect",
            }
        )

    # State-transition semantics are projected from the canonical lifecycle model,
    # not re-derived here from business_modality + state_effects.
    has_state_effects = bool(_rows(behavior.get("state_effects")))
    if not units and not has_state_effects and _text(behavior.get("business_modality")):
        units.append(
            {
                "source_unit_id": f"behavior:{behavior_id}:business-rule",
                "kind": "business_rule",
            }
        )
    return units


def _behavior_obligation(
    behavior: dict[str, Any],
    unit: dict[str, Any],
) -> dict[str, Any] | None:
    evidence = _normalize_evidence(behavior.get("evidence"))
    if not evidence:
        return None

    behavior_id = _text(behavior.get("behavior_id"))
    operation = _text(behavior.get("operation_ref"))
    actors = _strings(behavior.get("actor_refs"))
    objects = _strings(behavior.get("object_refs"))
    preconditions = _copy_preconditions(behavior.get("preconditions"))
    source_refs = _strings(behavior.get("source_refs"))
    kind = _text(unit.get("kind"))
    constraints = [
        value
        for value in (
            f"condition_combinator={_text(behavior.get('condition_combinator'))}"
            if _text(behavior.get("condition_combinator"))
            else "",
            *[f"exception={value}" for value in _strings(behavior.get("exceptions"))],
        )
        if value
    ]
    object_label = " / ".join(objects)
    actor_label = " / ".join(actors) or "适用角色"

    if kind == "authorization":
        decision = _text(behavior.get("permission_decision")).upper()
        return _base_obligation(
            source_unit_id=_text(unit.get("source_unit_id")),
            kind=kind,
            source_behavior_id=behavior_id,
            title=f"{actor_label}对{object_label}执行“{operation}”的授权边界",
            objective="验证来源明确声明的角色、操作与业务对象授权决策。",
            operation_ref=operation,
            actor_refs=actors,
            object_refs=objects,
            preconditions=preconditions,
            expected_outcomes=[
                {
                    "kind": "authorization_decision",
                    "decision": decision,
                }
            ],
            evidence=evidence,
            source_refs=source_refs,
            constraints=constraints,
        )

    if kind == "side_effect":
        outcomes: list[dict[str, Any]] = [
            {"kind": "postcondition", "statement": value}
            for value in _strings(behavior.get("expected_effects"))
        ]
        outcomes.extend(
            {
                "kind": "data_effect",
                "statement": _text(row.get("statement") or row.get("raw")),
                "field": _text(row.get("field")),
                "object": _text(row.get("object")),
            }
            for row in _rows(behavior.get("data_effects"))
            if _text(row.get("statement") or row.get("raw"))
        )
        outcomes.extend(
            {"kind": "compensation", "statement": value}
            for value in _strings(behavior.get("compensations"))
        )
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for outcome in outcomes:
            key = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(outcome)
        if not deduped:
            return None
        return _base_obligation(
            source_unit_id=_text(unit.get("source_unit_id")),
            kind=kind,
            source_behavior_id=behavior_id,
            title=f"{object_label}执行“{operation}”后的业务结果",
            objective="验证来源明确声明的后置条件、数据副作用或补偿结果。",
            operation_ref=operation,
            actor_refs=actors,
            object_refs=objects,
            preconditions=preconditions,
            expected_outcomes=deduped,
            evidence=evidence,
            source_refs=source_refs,
            constraints=constraints,
        )

    if kind == "business_rule":
        modality = _text(behavior.get("business_modality")).upper()
        if not modality:
            return None
        return _base_obligation(
            source_unit_id=_text(unit.get("source_unit_id")),
            kind=kind,
            source_behavior_id=behavior_id,
            title=f"{object_label}执行“{operation}”的业务规则",
            objective="验证来源明确声明的业务规则模态在给定条件下得到满足。",
            operation_ref=operation,
            actor_refs=actors,
            object_refs=objects,
            preconditions=preconditions,
            expected_outcomes=[
                {
                    "kind": "business_modality",
                    "modality": modality,
                }
            ],
            evidence=evidence,
            source_refs=source_refs,
            constraints=constraints,
        )
    return None


def _lifecycle_source_units(model: dict[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for lifecycle in _rows(model.get("lifecycles")):
        object_ref = _text(lifecycle.get("object_ref"))
        for transition in _rows(lifecycle.get("transitions")):
            transition_id = _text(transition.get("transition_id"))
            from_state = _text(transition.get("from_state"))
            to_state = _text(transition.get("to_state"))
            transition_kind = _text(transition.get("transition_kind")).upper()
            if (
                not transition_id
                or not object_ref
                or not from_state
                or not to_state
                or _text(transition.get("completeness")).upper() != "COMPLETE"
                or transition_kind not in {"ALLOWED", "FORBIDDEN"}
            ):
                continue
            units.append(
                {
                    "source_unit_id": f"lifecycle:{transition_id}",
                    "kind": "lifecycle_transition",
                    "object_ref": object_ref,
                    "transition": transition,
                }
            )
    return units


def _lifecycle_obligation(unit: dict[str, Any]) -> dict[str, Any] | None:
    transition = _dict(unit.get("transition"))
    evidence = _normalize_evidence(transition.get("evidence"))
    if not evidence:
        return None

    transition_id = _text(transition.get("transition_id"))
    object_ref = _text(unit.get("object_ref"))
    from_state = _text(transition.get("from_state"))
    to_state = _text(transition.get("to_state"))
    operation = _text(transition.get("operation_ref") or transition.get("event"))
    transition_kind = _text(transition.get("transition_kind")).upper()
    decision_cn = "允许" if transition_kind == "ALLOWED" else "禁止"
    operation_label = operation or "状态变化"
    return _base_obligation(
        source_unit_id=_text(unit.get("source_unit_id")),
        kind="lifecycle_transition",
        source_transition_id=transition_id,
        title=f"{object_ref}：{from_state} → {to_state} 状态规则",
        objective=(
            f"验证业务资料明确声明：在 {from_state} 状态执行“{operation_label}”时"
            f"{decision_cn}进入 {to_state}。"
        ),
        operation_ref=operation,
        actor_refs=[],
        object_refs=[object_ref],
        preconditions=[
            {"kind": "state", "object_ref": object_ref, "state": from_state},
            *_copy_preconditions(transition.get("conditions")),
        ],
        expected_outcomes=[
            {
                "kind": "lifecycle_transition",
                "transition_kind": transition_kind,
                "from_state": from_state,
                "to_state": to_state,
            }
        ],
        evidence=evidence,
        source_refs=_strings(transition.get("fact_refs")),
        constraints=[
            *[f"condition={value}" for value in _strings(transition.get("conditions"))],
            *[f"exception={value}" for value in _strings(transition.get("exceptions"))],
        ],
    )


def project_test_obligations(asset: dict[str, Any]) -> dict[str, Any]:
    """Project source-backed Test Obligations without executing any target system."""

    model = _dict(asset.get("enterprise_understanding_model"))
    eligible_units: list[dict[str, Any]] = []
    obligations: list[dict[str, Any]] = []
    unsupported_behavior_count = 0

    for behavior in _rows(model.get("business_behaviors")):
        units = _behavior_source_units(behavior)
        if not units:
            if _behavior_is_formal(behavior):
                unsupported_behavior_count += 1
            continue
        eligible_units.extend(units)
        for unit in units:
            obligation = _behavior_obligation(behavior, unit)
            if obligation is not None:
                obligations.append(obligation)

    lifecycle_units = _lifecycle_source_units(model)
    eligible_units.extend(lifecycle_units)
    for unit in lifecycle_units:
        obligation = _lifecycle_obligation(unit)
        if obligation is not None:
            obligations.append(obligation)

    obligations.sort(
        key=lambda item: (
            _text(item.get("obligation_kind")),
            _text(item.get("obligation_id")),
        )
    )
    eligible_ids = sorted(
        {
            _text(unit.get("source_unit_id"))
            for unit in eligible_units
            if _text(unit.get("source_unit_id"))
        }
    )
    obligated_ids = {
        _text(item.get("source_unit_id"))
        for item in obligations
        if _text(item.get("source_unit_id"))
    }
    uncovered_ids = sorted(set(eligible_ids) - obligated_ids)

    return {
        "schema": PROJECTION_SCHEMA,
        "implemented_obligation_kinds": list(_IMPLEMENTED_KINDS),
        "eligible_source_unit_ids": eligible_ids,
        "uncovered_source_unit_ids": uncovered_ids,
        "suppressed_without_evidence_count": len(uncovered_ids),
        "unsupported_formal_behavior_count": unsupported_behavior_count,
        "obligations": obligations,
    }
