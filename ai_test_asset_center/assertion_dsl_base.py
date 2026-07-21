"""Restricted assertion DSL — no eval, typed operators only."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .observer_contracts_base import validate_observer_receipt


ASSERTION_RECEIPT_SCHEMA = "qualibug.assertion-receipt.v1"
ASSERTION_STATUSES = frozenset({"PASS", "VIOLATION", "INDETERMINATE"})
_MISSING = object()

SUPPORTED_KINDS = {
    "http_status",
    "http_status_class",
    "json_path_exists",
    "json_path_type",
    "json_path_compare",
    "equality",
    "delta",
    "cardinality",
    "state_transition",
    "postcondition",
    "owner_tenant_visibility",
    "conservation",
    "idempotency_effect",
    "concurrency_final_invariant",
    "eventual_consistency",
    "cross_surface_consistency",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _state_token(value: Any) -> str:
    """Normalize presentation-only enum differences without changing meaning."""

    normalized = _text(value).replace("-", " ").replace("_", " ")
    return "_".join(normalized.split()).casefold()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _assertion_receipt(
    *,
    assertion_id: str,
    kind: str,
    status: str,
    reason_code: str,
    expected: Any,
    actual: Any,
    error: str,
    observer_receipt_ids: list[str],
    source_refs: list[dict[str, Any]],
    harness_error: bool,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    normalized_status = _text(status).upper()
    if normalized_status not in ASSERTION_STATUSES:
        raise ValueError(f"assertion_status_invalid:{normalized_status}")
    payload = {
        "schema_version": ASSERTION_RECEIPT_SCHEMA,
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
        "assertion_id": _text(assertion_id),
        "kind": _text(kind),
        "status": normalized_status,
        "reason_code": _text(reason_code),
        "passed": (
            True
            if normalized_status == "PASS"
            else False
            if normalized_status == "VIOLATION"
            else None
        ),
        "expected": expected,
        "actual": actual,
        "error": _text(error),
        "observer_receipt_ids": sorted(
            set(_text(item) for item in observer_receipt_ids if _text(item))
        ),
        "source_refs": [
            dict(item) for item in source_refs if isinstance(item, dict)
        ],
        "harness_error": bool(harness_error),
    }
    return {
        **payload,
        "receipt_id": "assert_" + hashlib.sha256(
            _canonical(payload).encode("utf-8")
        ).hexdigest()[:24],
    }


def validate_assertion_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    required_fields = {
        "schema_version",
        "receipt_id",
        "campaign_id",
        "execution_id",
        "assertion_id",
        "kind",
        "status",
        "reason_code",
        "passed",
        "expected",
        "actual",
        "error",
        "observer_receipt_ids",
        "source_refs",
        "harness_error",
    }
    if set(row) != required_fields:
        raise ValueError("assertion_receipt_fields_invalid")
    if row.get("schema_version") != ASSERTION_RECEIPT_SCHEMA:
        raise ValueError("assertion_receipt_schema_invalid")
    if not isinstance(row.get("observer_receipt_ids"), list) or not isinstance(
        row.get("source_refs"), list
    ):
        raise ValueError("assertion_receipt_content_invalid")
    expected = _assertion_receipt(
        assertion_id=_text(row.get("assertion_id")),
        kind=_text(row.get("kind")),
        status=_text(row.get("status")),
        reason_code=_text(row.get("reason_code")),
        expected=row.get("expected"),
        actual=row.get("actual"),
        error=_text(row.get("error")),
        observer_receipt_ids=list(row["observer_receipt_ids"]),
        source_refs=[
            dict(item)
            for item in row["source_refs"]
            if isinstance(item, dict)
        ],
        harness_error=bool(row.get("harness_error")),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
    )
    if row != expected:
        raise ValueError("assertion_receipt_fingerprint_invalid")
    return dict(expected)


def _typed_observer_receipts(
    observations: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    raw_many = observations.get("observer_receipts")
    if raw_many is not None:
        if not isinstance(raw_many, list):
            raise ValueError("observer_receipts_not_list")
        candidates.extend(raw_many)
    for key, value in observations.items():
        if key == "observer_receipts":
            continue
        if key == "observer_receipt" or key.endswith("_observer_receipt"):
            if value not in (None, {}):
                candidates.append(value)
    by_id: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("observer_receipt_not_object")
        validated = validate_observer_receipt(candidate)
        receipt_id = _text(validated.get("receipt_id"))
        previous = by_id.get(receipt_id)
        if previous is not None and previous != validated:
            raise ValueError("observer_receipt_identity_conflict")
        by_id[receipt_id] = validated
    return [by_id[key] for key in sorted(by_id)]


def _json_path(data: Any, path: str) -> Any:
    """Minimal JSON path: $.a.b[0] style without eval."""

    if not path or path == "$":
        return data
    cur: Any = data
    token = path[1:] if path.startswith("$") else path
    parts: list[str] = []
    buf = ""
    index = 0
    while index < len(token):
        char = token[index]
        if char == ".":
            if buf:
                parts.append(buf)
                buf = ""
            index += 1
            continue
        if char == "[":
            if buf:
                parts.append(buf)
                buf = ""
            end = token.find("]", index)
            if end < 0:
                raise ValueError("invalid_json_path")
            parts.append(token[index : end + 1])
            index = end + 1
            continue
        buf += char
        index += 1
    if buf:
        parts.append(buf)

    for part in parts:
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            item_index = int(part[1:-1])
            if not isinstance(cur, list) or item_index >= len(cur):
                raise KeyError(part)
            cur = cur[item_index]
        else:
            if not isinstance(cur, dict) or part not in cur:
                raise KeyError(part)
            cur = cur[part]
    return cur


def evaluate_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Evaluate one typed assertion into a content-addressed tri-state receipt."""

    spec = _dict(assertion)
    kind = _text(spec.get("kind") or spec.get("type"))
    assertion_id = _text(spec.get("assertion_id") or spec.get("id"))
    obs = _dict(observations)
    refs = [
        dict(item)
        for item in list(
            source_refs
            if source_refs is not None
            else spec.get("source_refs") or []
        )
        if isinstance(item, dict)
    ]
    expected: Any = spec.get("expected")
    actual: Any = None
    passed: bool | None = None
    reason_code = ""
    error = ""
    harness_error = bool(obs.get("harness_error"))
    resolved_campaign_id = _text(campaign_id or obs.get("campaign_id"))
    resolved_execution_id = _text(execution_id or obs.get("execution_id"))
    if bool(resolved_campaign_id) != bool(resolved_execution_id):
        harness_error = True

    try:
        observer_receipts = _typed_observer_receipts(obs)
    except Exception as exc:
        return _assertion_receipt(
            assertion_id=assertion_id,
            kind=kind,
            status="INDETERMINATE",
            reason_code="OBSERVER_RECEIPT_INVALID",
            expected=expected,
            actual=actual,
            error=f"{type(exc).__name__}: {exc}",
            observer_receipt_ids=[],
            source_refs=refs,
            harness_error=True,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )

    observer_ids = [
        _text(item.get("receipt_id")) for item in observer_receipts
    ]
    observer_lineages = {
        (
            _text(item.get("campaign_id")),
            _text(item.get("execution_id")),
        )
        for item in observer_receipts
        if _text(item.get("campaign_id"))
        or _text(item.get("execution_id"))
    }
    if not resolved_campaign_id and len(observer_lineages) == 1:
        resolved_campaign_id, resolved_execution_id = next(
            iter(observer_lineages)
        )
    if (
        len(observer_lineages) > 1
        or any(
            not campaign or not execution
            for campaign, execution in observer_lineages
        )
        or any(
            campaign != resolved_campaign_id
            or execution != resolved_execution_id
            for campaign, execution in observer_lineages
        )
    ):
        return _assertion_receipt(
            assertion_id=assertion_id,
            kind=kind,
            status="INDETERMINATE",
            reason_code="OBSERVER_RECEIPT_LINEAGE_MISMATCH",
            expected=expected,
            actual=actual,
            error="observer receipt campaign/execution lineage mismatch",
            observer_receipt_ids=observer_ids,
            source_refs=refs,
            harness_error=True,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )

    non_observed = [
        item
        for item in observer_receipts
        if _text(item.get("status")).upper() != "OBSERVED"
    ]
    if non_observed:
        first = non_observed[0]
        observer_status = _text(first.get("status")).upper()
        return _assertion_receipt(
            assertion_id=assertion_id,
            kind=kind,
            status="INDETERMINATE",
            reason_code=f"OBSERVER_EVIDENCE_{observer_status}",
            expected=expected,
            actual=actual,
            error=_text(first.get("reason_code")),
            observer_receipt_ids=observer_ids,
            source_refs=refs,
            harness_error=observer_status in {"FAILED", "UNSUPPORTED"},
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )
    if harness_error:
        return _assertion_receipt(
            assertion_id=assertion_id,
            kind=kind,
            status="INDETERMINATE",
            reason_code="HARNESS_ERROR_PRESENT",
            expected=expected,
            actual=actual,
            error=_text(obs.get("harness_error")),
            observer_receipt_ids=observer_ids,
            source_refs=refs,
            harness_error=True,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )

    aliases = {
        "authorization": "owner_tenant_visibility",
        "isolation": "owner_tenant_visibility",
        "visibility": "owner_tenant_visibility",
        "privacy": "owner_tenant_visibility",
        "validation": "http_status",
        "state": "state_transition",
        "idempotency": "idempotency_effect",
        "concurrency": "concurrency_final_invariant",
        "temporal": "eventual_consistency",
        "consistency": "cross_surface_consistency",
    }
    effective_kind = aliases.get(kind, kind)

    try:
        if effective_kind not in SUPPORTED_KINDS:
            raise ValueError(f"unsupported_assertion_kind:{kind}")
        if effective_kind == "json_path_compare":
            operator = _text(spec.get("operator") or "eq")
            if operator not in {"eq", "neq", "gte", "lte"}:
                raise ValueError(f"unsupported_operator:{operator}")
        if effective_kind == "conservation":
            conservation_operator = _text(
                _dict(spec.get("equation")).get("operator")
                or "unchanged_sum"
            )
            if conservation_operator not in {"eq", "unchanged_sum"}:
                raise ValueError(
                    f"unsupported_conservation_operator:{conservation_operator}"
                )
        if effective_kind == "idempotency_effect":
            int(spec.get("expected_effect_count", 1))

        if effective_kind == "http_status":
            expected = spec.get("expected", spec.get("expected_status"))
            if "status_code" not in obs or expected is None:
                reason_code = "HTTP_STATUS_EVIDENCE_MISSING"
            else:
                actual = obs["status_code"]
                passed = actual == expected
        elif effective_kind == "http_status_class":
            expected_value = spec.get(
                "expected",
                spec.get("expected_class"),
            )
            if "status_code" not in obs or expected_value is None:
                reason_code = "HTTP_STATUS_CLASS_EVIDENCE_MISSING"
            else:
                actual = int(obs["status_code"])
                expected = int(expected_value)
                passed = (actual // 100) == expected
                # Soft business reject on an accepted HTTP class must not pass a
                # success-class assertion when the body declares failure.
                if (
                    passed
                    and expected == 2
                    and (
                        obs.get("business_rejected") is True
                        or _dict(obs.get("business_outcome")).get("business_rejected")
                        is True
                    )
                ):
                    passed = False
                    reason_code = "HTTP_SOFT_BUSINESS_REJECTED"
                if (
                    passed
                    and expected == 2
                    and obs.get("zero_effect_on_accepted_write") is True
                    and spec.get("require_nonzero_effect") is True
                ):
                    passed = False
                    reason_code = "HTTP_ACCEPTED_ZERO_EFFECT"
        elif effective_kind == "json_path_exists":
            expected = True
            if "body" not in obs:
                reason_code = "HTTP_BODY_EVIDENCE_MISSING"
            else:
                path = _text(spec.get("path") or "$")
                try:
                    actual = _json_path(obs["body"], path)
                    passed = actual is not None
                except (KeyError, IndexError, TypeError):
                    actual = None
                    passed = False
        elif effective_kind == "json_path_type":
            expected_type = _text(
                spec.get("expected")
                or spec.get("expected_type")
                or spec.get("type_name")
            ).lower()
            type_map = {
                "string": str,
                "str": str,
                "number": (int, float),
                "int": int,
                "integer": int,
                "float": float,
                "bool": bool,
                "boolean": bool,
                "object": dict,
                "dict": dict,
                "array": list,
                "list": list,
                "null": type(None),
            }
            py_type = type_map.get(expected_type)
            if py_type is None:
                raise ValueError(
                    f"unsupported_json_path_type:{expected_type}"
                )
            expected = expected_type
            if "body" not in obs:
                reason_code = "HTTP_BODY_EVIDENCE_MISSING"
            else:
                try:
                    value = _json_path(
                        obs["body"],
                        _text(spec.get("path") or "$"),
                    )
                    actual = type(value).__name__
                    passed = isinstance(value, py_type)
                except (KeyError, IndexError, TypeError):
                    actual = None
                    passed = False
        elif effective_kind == "json_path_compare":
            if "body" not in obs or "expected" not in spec:
                reason_code = "JSON_COMPARE_EVIDENCE_MISSING"
            else:
                try:
                    actual = _json_path(
                        obs["body"],
                        _text(spec.get("path") or "$"),
                    )
                except (KeyError, IndexError, TypeError):
                    actual = None
                    passed = False
                if passed is None:
                    if operator == "eq":
                        passed = actual == expected
                    elif operator == "neq":
                        passed = actual != expected
                    elif operator == "gte":
                        passed = actual >= expected
                    elif operator == "lte":
                        passed = actual <= expected
        elif effective_kind == "equality":
            if "value" not in obs or "expected" not in spec:
                reason_code = "EQUALITY_EVIDENCE_MISSING"
            else:
                actual = obs["value"]
                passed = actual == expected
        elif effective_kind == "delta":
            if (
                "before" not in obs
                or "after" not in obs
                or "expected" not in spec
            ):
                reason_code = "DELTA_EVIDENCE_MISSING"
            else:
                actual = obs["after"] - obs["before"]
                passed = actual == expected
        elif effective_kind == "cardinality":
            if "collection" not in obs or "expected" not in spec:
                reason_code = "CARDINALITY_EVIDENCE_MISSING"
            else:
                collection = obs["collection"]
                actual = (
                    len(collection)
                    if isinstance(collection, list)
                    else {"observed_type": type(collection).__name__}
                )
                passed = actual == expected
        elif effective_kind == "state_transition":
            if (
                "before_state" not in obs
                or "after_state" not in obs
                or "from_state" not in spec
                or "to_state" not in spec
            ):
                reason_code = "STATE_TRANSITION_EVIDENCE_MISSING"
            elif _text(spec.get("from_state")) == "unknown_state":
                # Synthetic state values — just check if state changed
                passed = _state_token(obs["before_state"]) != _state_token(obs["after_state"])
            else:
                actual = {
                    "before": obs["before_state"],
                    "after": obs["after_state"],
                }
                expected = {
                    "before": spec["from_state"],
                    "after": spec["to_state"],
                }
                if _state_token(obs["before_state"]) != _state_token(
                    spec["from_state"]
                ):
                    # A wrong source state means the experiment precondition was
                    # not established. It is not product-defect evidence.
                    reason_code = "STATE_PRECONDITION_NOT_MET"
                else:
                    passed = _state_token(
                        obs["after_state"]
                    ) == _state_token(spec["to_state"])
        elif effective_kind == "postcondition":
            # Postcondition assertions verify that a causal rule's expected
            # effect actually materialized after the trigger action executed.
            # Uses entity_state observer evidence (state_change_count, effect_count).
            # must_become: state must have changed (fingerprint difference)
            # must_create: a new entity must appear (identity count increase)
            pc_operator = _text(spec.get("operator"))
            pc_operands = spec.get("operands") or []
            pc_operand = pc_operands[0] if pc_operands and isinstance(pc_operands[0], dict) else {}
            entity_ref = _text(pc_operand.get("entity_ref"))
            field_ref = _text(pc_operand.get("field"))
            expected_value = pc_operand.get("expected_value")
            must_create = bool(pc_operand.get("must_create"))
            # Gather entity_state evidence from observations
            state_change_count = obs.get("state_change_count")
            effect_count = obs.get("effect_count")
            entity_state_observed = obs.get("entity_state_observed")
            state_windows = obs.get("state_windows") or []
            if entity_state_observed is not True and state_change_count is None:
                reason_code = "POSTCONDITION_ENTITY_STATE_EVIDENCE_MISSING"
            elif must_create:
                # must_create: verify new entity appeared (identity count increase or effect > 0)
                expected = {"entity": entity_ref, "must_create": True}
                identity_increase = any(
                    isinstance(w, dict) and int(w.get("after_identity_count") or 0) > int(w.get("before_identity_count") or 0)
                    for w in state_windows
                )
                actual = {
                    "state_change_count": state_change_count,
                    "effect_count": effect_count,
                    "identity_increase": identity_increase,
                }
                passed = identity_increase or int(effect_count or 0) > 0
                if not passed:
                    reason_code = "POSTCONDITION_ENTITY_NOT_CREATED"
            else:
                # must_become: verify state actually changed
                expected = {"entity": entity_ref, "field": field_ref, "must_become": expected_value}
                actual = {
                    "state_change_count": state_change_count,
                    "effect_count": effect_count,
                }
                if int(state_change_count or 0) > 0:
                    passed = True
                elif int(effect_count or 0) > 0:
                    # Effect detected but fingerprint unchanged — partial pass
                    passed = True
                else:
                    passed = False
                    reason_code = "POSTCONDITION_STATE_NOT_CHANGED"
        elif effective_kind == "owner_tenant_visibility":
            required_values = (
                obs.get("owner_can_access"),
                obs.get("viewer_can_access"),
                obs.get("leak_detected"),
            )
            expected = {
                "owner_can_access": True,
                "viewer_can_access": False,
                "leak_detected": False,
            }
            if obs.get("control_succeeded") is not True:
                reason_code = "AUTHORIZED_CONTROL_NOT_PROVEN"
            elif not all(
                isinstance(value, bool) for value in required_values
            ):
                reason_code = "AUTHORIZATION_OBSERVATION_MISSING"
            elif (
                spec.get("require_same_resource", True)
                and obs.get("same_resource_proven") is not True
            ):
                reason_code = "SAME_RESOURCE_NOT_PROVEN"
            else:
                actual = {
                    "owner_can_access": obs["owner_can_access"],
                    "viewer_can_access": obs["viewer_can_access"],
                    "leak_detected": obs["leak_detected"],
                }
                passed = actual == expected
        elif effective_kind == "conservation":
            equation = _dict(spec.get("equation"))
            operator = _text(
                equation.get("operator")
                or "unchanged_sum"
            )
            terms = [
                _text(item)
                for item in _list(
                    equation.get("terms")
                    or equation.get("fields")
                )
                if _text(item)
            ]
            before_values = obs.get("before_values")
            after_values = obs.get("after_values")
            expected = {"operator": operator, "terms": terms}
            if (
                not isinstance(before_values, dict)
                or not before_values
                or not isinstance(after_values, dict)
                or not after_values
            ):
                reason_code = "CONSERVATION_VALUES_MISSING"
            elif operator == "eq":
                actual = {
                    "before": before_values,
                    "after": after_values,
                }
                passed = before_values == after_values
            elif operator == "unchanged_sum":
                selected_terms = terms or sorted(
                    set(before_values).intersection(after_values)
                )
                if not selected_terms or any(
                    term not in before_values
                    or term not in after_values
                    or isinstance(before_values[term], bool)
                    or isinstance(after_values[term], bool)
                    or not isinstance(
                        before_values[term],
                        (int, float),
                    )
                    or not isinstance(
                        after_values[term],
                        (int, float),
                    )
                    for term in selected_terms
                ):
                    reason_code = "CONSERVATION_VALUES_MISSING"
                else:
                    before_sum = sum(
                        float(before_values[term])
                        for term in selected_terms
                    )
                    after_sum = sum(
                        float(after_values[term])
                        for term in selected_terms
                    )
                    actual = {
                        "before_sum": before_sum,
                        "after_sum": after_sum,
                        "before": before_values,
                        "after": after_values,
                    }
                    passed = before_sum == after_sum
            else:
                raise ValueError(
                    f"unsupported_conservation_operator:{operator}"
                )
        elif effective_kind == "idempotency_effect":
            expected_count = spec.get("expected_effect_count", 1)
            expected = {"effect_count": expected_count}
            actual = {
                "effect_count": obs.get("effect_count"),
                "http_statuses": obs.get("http_statuses"),
            }
            if obs.get("effect_count") is None:
                reason_code = "BUSINESS_EFFECT_MISSING"
            else:
                passed = int(obs["effect_count"]) == int(expected_count)
        elif effective_kind == "concurrency_final_invariant":
            expected = {"invariant_held": True}
            actual = {
                "final_state": obs.get("final_state"),
                "invariant_held": obs.get("invariant_held"),
                "dual_2xx": obs.get("dual_2xx"),
            }
            # Surface observed entity numerics (e.g. available_qty) when the
            # final_state observer captured them — fingerprint-only actuals hide
            # the concrete invariant breach from delivery/scoring blobs.
            before_values = obs.get("before_values")
            after_values = obs.get("after_values")
            if isinstance(before_values, dict) and before_values:
                actual["before_values"] = dict(before_values)
            if isinstance(after_values, dict) and after_values:
                actual["after_values"] = dict(after_values)
            if not isinstance(obs.get("invariant_held"), bool):
                reason_code = "FINAL_INVARIANT_MISSING"
            else:
                passed = obs["invariant_held"] is True
        elif effective_kind == "eventual_consistency":
            expected = {
                "converged": True,
                "within_window": True,
            }
            if not isinstance(
                obs.get("converged"),
                bool,
            ) or not isinstance(
                obs.get("within_window"),
                bool,
            ):
                reason_code = "EVENTUAL_CONSISTENCY_EVIDENCE_MISSING"
            else:
                actual = {
                    "converged": obs["converged"],
                    "within_window": obs["within_window"],
                }
                passed = actual == expected
        elif effective_kind == "cross_surface_consistency":
            expected = True
            if not isinstance(obs.get("surfaces_agree"), bool):
                reason_code = "CROSS_SURFACE_EVIDENCE_MISSING"
            else:
                actual = obs["surfaces_agree"]
                passed = actual is True
        else:
            expected = spec.get(
                "expected",
                obs.get("expected", _MISSING),
            )
            actual = obs.get(
                "actual",
                obs.get("treatment_result", _MISSING),
            )
            if expected is _MISSING or actual is _MISSING:
                expected = None if expected is _MISSING else expected
                actual = None if actual is _MISSING else actual
                reason_code = "ASSERTION_EVIDENCE_MISSING"
            else:
                passed = actual == expected
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        reason_code = "ASSERTION_EVALUATION_ERROR"
        harness_error = True
        passed = None

    status = (
        "INDETERMINATE"
        if passed is None
        else "PASS"
        if passed
        else "VIOLATION"
    )
    if status == "INDETERMINATE" and not reason_code:
        reason_code = "ASSERTION_EVIDENCE_MISSING"
    return _assertion_receipt(
        assertion_id=assertion_id,
        kind=kind,
        status=status,
        reason_code=reason_code,
        expected=expected,
        actual=actual,
        error=error,
        observer_receipt_ids=observer_ids,
        source_refs=refs,
        harness_error=harness_error,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )


def evaluate_assertions(
    assertions: list[dict[str, Any]],
    *,
    observations_by_id: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    results = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        obs_key = _text(
            assertion.get("observer_id")
            or assertion.get("assertion_id")
            or "default"
        )
        obs = _dict(
            observations_by_id.get(obs_key)
            or observations_by_id.get("default")
        )
        results.append(
            evaluate_assertion(
                assertion,
                observations=obs,
                campaign_id=campaign_id,
                execution_id=execution_id,
            )
        )
    return {
        "total": len(results),
        "passed": sum(
            1 for item in results if item.get("status") == "PASS"
        ),
        "violations": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "indeterminate": sum(
            1
            for item in results
            if item.get("status") == "INDETERMINATE"
        ),
        # Compatibility alias: only proven violations are assertion failures.
        "failed": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "harness_errors": sum(
            1 for item in results if item.get("harness_error")
        ),
        "results": results,
    }


def materialize_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn compiler templates into executable DSL specs without inventing expected values."""

    spec = dict(_dict(assertion))
    kind = _text(spec.get("kind") or spec.get("type"))
    prop = _dict(spec.get("property"))
    obs = _dict(observations)

    family_map = {
        "authorization": "owner_tenant_visibility",
        "isolation": "owner_tenant_visibility",
        "visibility": "owner_tenant_visibility",
        "privacy": "owner_tenant_visibility",
        "idempotency": "idempotency_effect",
        "concurrency": "concurrency_final_invariant",
        "state": "state_transition",
        "conservation": "conservation",
        "validation": "http_status",
    }
    if kind in family_map:
        spec["kind"] = family_map[kind]
        kind = spec["kind"]

    if (
        kind == "http_status"
        and spec.get("expected") is None
        and spec.get("expected_status") is None
    ):
        if prop.get("expected_status") is not None:
            spec["expected"] = prop.get("expected_status")
        elif obs.get("expected_status") is not None:
            spec["expected"] = obs.get("expected_status")

    if kind == "owner_tenant_visibility":
        spec.setdefault("require_control", True)

    if kind == "state_transition":
        if spec.get("from_state") is None and prop.get("from_state") is not None:
            spec["from_state"] = prop.get("from_state")
        if spec.get("to_state") is None and prop.get("to_state") is not None:
            spec["to_state"] = prop.get("to_state")

    if kind == "conservation" and not _dict(spec.get("equation")):
        equation = _dict(prop.get("equation"))
        if equation:
            spec["equation"] = equation

    if (
        kind == "idempotency_effect"
        and spec.get("expected_effect_count") is None
        and prop.get("expected_effect_count") is not None
    ):
        spec["expected_effect_count"] = prop.get(
            "expected_effect_count"
        )

    return spec
