"""Typed observer capability, receipt, and evidence-sufficiency contracts.

An HTTP response proves that transport was observed. It does not by itself
prove a protected business resource was visible or a write had an effect.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = "qualibug.observer-receipt.v1"
OBSERVER_STATUSES = frozenset({"OBSERVED", "INDETERMINATE", "FAILED", "UNSUPPORTED"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:20]


def _receipt(
    *,
    observer_id: str,
    status: str,
    reason_code: str = "",
    evidence: dict[str, Any] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    normalized_status = _text(status).upper()
    if normalized_status not in OBSERVER_STATUSES:
        raise ValueError(f"observer_status_invalid:{normalized_status}")
    safe_evidence = dict(evidence or {})
    receipt_id = "obs_" + _fingerprint({
        "observer_id": observer_id,
        "status": normalized_status,
        "reason_code": reason_code,
        "evidence": safe_evidence,
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
        "observer_id": _text(observer_id),
        "status": normalized_status,
        "reason_code": _text(reason_code),
        "evidence": safe_evidence,
    }


def build_observer_receipt(
    *,
    observer_id: str,
    status: str,
    reason_code: str = "",
    evidence: dict[str, Any] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Create one content-addressed typed observer receipt."""

    if not _text(observer_id):
        raise ValueError("observer_id_missing")
    return _receipt(
        observer_id=observer_id,
        status=status,
        reason_code=reason_code,
        evidence=evidence,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )


def validate_observer_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate schema, status, identity, and content fingerprint strictly."""

    row = _dict(receipt)
    required_fields = {
        "schema_version",
        "receipt_id",
        "campaign_id",
        "execution_id",
        "observer_id",
        "status",
        "reason_code",
        "evidence",
    }
    if set(row) != required_fields:
        raise ValueError("observer_receipt_fields_invalid")
    if row.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("observer_receipt_schema_invalid")
    observer_id = _text(row.get("observer_id"))
    if not observer_id or not isinstance(row.get("evidence"), dict):
        raise ValueError("observer_receipt_content_invalid")
    expected = _receipt(
        observer_id=observer_id,
        status=_text(row.get("status")),
        reason_code=_text(row.get("reason_code")),
        evidence=dict(row["evidence"]),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
    )
    if row != expected:
        raise ValueError("observer_receipt_fingerprint_invalid")
    return dict(expected)


def bind_observer_receipt_lineage(
    receipt: dict[str, Any],
    *,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Bind a typed observer receipt to exactly one runtime execution."""

    resolved_campaign_id = _text(campaign_id)
    resolved_execution_id = _text(execution_id)
    if not resolved_campaign_id or not resolved_execution_id:
        raise ValueError("observer_receipt_lineage_missing")
    validated = validate_observer_receipt(receipt)
    existing_campaign_id = _text(validated.get("campaign_id"))
    existing_execution_id = _text(validated.get("execution_id"))
    if existing_campaign_id and existing_campaign_id != resolved_campaign_id:
        raise ValueError("observer_receipt_campaign_mismatch")
    if existing_execution_id and existing_execution_id != resolved_execution_id:
        raise ValueError("observer_receipt_execution_mismatch")
    return _receipt(
        observer_id=_text(validated.get("observer_id")),
        status=_text(validated.get("status")),
        reason_code=_text(validated.get("reason_code")),
        evidence=dict(validated["evidence"]),
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )


# ``implemented`` means the current candidate runtime emits a typed receipt for
# this observer. Declaring a surface in Behavior IR is not implementation.
OBSERVER_REGISTRY: dict[str, dict[str, Any]] = {
    "http_response": {
        "surface": "http_api",
        "adapter": "http_api",
        "implemented": True,
    },
    "actor_identity": {
        "surface": "identity_context",
        "adapter": "http_api",
        "implemented": True,
    },
    "authorization_comparison": {
        "surface": "http_api",
        "adapter": "http_api",
        "implemented": True,
    },
    "resource_ownership": {
        "surface": "http_api",
        "adapter": "http_api",
        "implemented": True,
    },
    "business_effect": {
        "surface": "business_effect",
        "adapter": "http_api",
        "implemented": True,
    },
    "entity_state": {
        "surface": "business_state",
        "adapter": "http_api",
        "implemented": True,
    },
    "before_state": {
        "surface": "business_state",
        "adapter": "http_api",
        "implemented": True,
    },
    "after_state": {
        "surface": "business_state",
        "adapter": "http_api",
        "implemented": True,
    },
    "final_state": {
        "surface": "business_state",
        "adapter": "http_api",
        "implemented": True,
    },
    "barrier_timeline": {
        "surface": "concurrency_barrier",
        "adapter": "http_api",
        "implemented": True,
    },
    "typed_assertion": {
        "surface": "contract_assertion",
        "adapter": "http_api",
        "implemented": True,
    },
    "source_invariant": {
        "surface": "source_contract",
        "adapter": "http_api",
        "implemented": True,
    },
    "temporal_window": {
        "surface": "temporal_convergence",
        "adapter": "http_api",
        "implemented": True,
    },
    "write_observer": {
        "surface": "business_effect",
        "adapter": "http_api",
        "implemented": True,
    },
}


def compile_observer_requirements(
    observer_ids: list[str],
    *,
    risk_family: str,
    available_adapters: set[str],
) -> tuple[list[dict[str, Any]], str, str]:
    """Return typed requirements or one stable compile blocker."""

    required = [_text(value) for value in observer_ids if _text(value)]
    if _text(risk_family) in {"authorization", "isolation", "visibility"}:
        required.append("authorization_comparison")
    required = list(dict.fromkeys(required))
    if not required:
        return [], "BLOCKED_MISSING_OBSERVER", "none"

    compiled: list[dict[str, Any]] = []
    for observer_id in required:
        contract = OBSERVER_REGISTRY.get(observer_id)
        if not contract or contract.get("implemented") is not True:
            return [], "BLOCKED_MISSING_OBSERVER", observer_id
        adapter = _text(contract.get("adapter"))
        if adapter not in available_adapters:
            return [], "BLOCKED_UNSUPPORTED_ADAPTER", adapter or observer_id
        compiled.append({
            "observer_id": observer_id,
            "surface": _text(contract.get("surface")),
            "adapter": adapter,
            "receipt_schema": SCHEMA_VERSION,
            "required_status": "OBSERVED",
        })
    return compiled, "", ""


def validate_observer_declarations(
    observers: list[dict[str, Any]],
    *,
    risk_family: str,
    available_adapters: set[str],
) -> tuple[str, str]:
    """Validate persisted/manual experiments against the current registry."""

    declared = [
        _text(_dict(observer).get("observer_id"))
        for observer in observers
        if _text(_dict(observer).get("observer_id"))
    ]
    compiled, reason_code, detail = compile_observer_requirements(
        declared,
        risk_family=risk_family,
        available_adapters=available_adapters,
    )
    if reason_code:
        return reason_code, detail
    declared_set = set(declared)
    for requirement in compiled:
        observer_id = _text(requirement.get("observer_id"))
        if observer_id not in declared_set:
            return "BLOCKED_MISSING_OBSERVER", observer_id
    return "", ""


def _response_status(observation: dict[str, Any]) -> int:
    try:
        return int(_dict(observation).get("status_code") or 0)
    except (TypeError, ValueError):
        return 0


def _raw_http_status(observation: dict[str, Any]) -> int:
    row = _dict(observation)
    try:
        return int(row.get("status_code") or row.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def _is_success(observation: dict[str, Any]) -> bool:
    return 200 <= _response_status(observation) < 300


def _is_error_envelope(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    normalized_keys = {
        re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        for key in value
    }
    error_keys = {"error", "errors", "message", "detail", "error_code"}
    identity_keys = {
        key for key in normalized_keys
        if key in {"id", "uuid", "key"} or key.endswith("_id")
    }
    return bool(normalized_keys.intersection(error_keys)) and not identity_keys


def _meaningful_resource_body(observation: dict[str, Any]) -> bool:
    method = _text(observation.get("method")).upper()
    if method == "HEAD" or _response_status(observation) == 204:
        return False
    body = observation.get("body")
    if isinstance(body, dict):
        return bool(body) and not _is_error_envelope(body)
    if isinstance(body, list):
        return bool(body)
    return False


def _identity_fingerprints(value: Any) -> set[str]:
    result: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            scalar_identities: list[tuple[str, Any]] = []
            for key, child in node.items():
                normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
                if isinstance(child, (str, int, float)) and str(child).strip():
                    if normalized_key in {"id", "uuid", "key"}:
                        scalar_identities.append((normalized_key, child))
            if not scalar_identities:
                fallback_identities = []
                for key, child in node.items():
                    normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
                    if (
                        normalized_key.endswith("_id")
                        and isinstance(child, (str, int, float))
                        and str(child).strip()
                    ):
                        fallback_identities.append((normalized_key, child))
                if len(fallback_identities) == 1:
                    scalar_identities = fallback_identities
            if scalar_identities:
                key, child = sorted(
                    scalar_identities,
                    key=lambda item: ({"id": 0, "uuid": 1, "key": 2}.get(item[0], 3), item[0]),
                )[0]
                result.add(_fingerprint({"key": key, "value": child}))
                return
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def _server_managed_field(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    return normalized in {
        "createdat",
        "updatedat",
        "createdtime",
        "updatedtime",
        "modifiedat",
        "modifiedtime",
        "timestamp",
    }


def _without_server_managed_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_server_managed_fields(child)
            for key, child in sorted(value.items())
            if not _server_managed_field(key)
        }
    if isinstance(value, list):
        return sorted(
            (_without_server_managed_fields(child) for child in value),
            key=_canonical,
        )
    return value


def _entity_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("records", "data", "items", "results", "rows"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [dict(item) for item in nested if isinstance(item, dict)]
        return [dict(value)]
    return []


def _entity_identity_key(entity: dict[str, Any]) -> str:
    scalar_identities: list[tuple[str, Any]] = []
    for key, child in entity.items():
        normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if isinstance(child, (str, int, float)) and str(child).strip():
            if normalized_key in {"id", "uuid", "key"}:
                scalar_identities.append((normalized_key, child))
    if not scalar_identities:
        fallback_identities = []
        for key, child in entity.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if (
                normalized_key.endswith("_id")
                and isinstance(child, (str, int, float))
                and str(child).strip()
            ):
                fallback_identities.append((normalized_key, child))
        if len(fallback_identities) == 1:
            scalar_identities = fallback_identities
    if not scalar_identities:
        return ""
    key, child = sorted(
        scalar_identities,
        key=lambda item: ({"id": 0, "uuid": 1, "key": 2}.get(item[0], 3), item[0]),
    )[0]
    return _fingerprint({"key": key, "value": child})


def _business_field_change_count(before_body: Any, after_body: Any) -> int:
    before_rows = {
        key: _without_server_managed_fields(row)
        for row in _entity_rows(before_body)
        for key in [_entity_identity_key(row)]
        if key
    }
    after_rows = {
        key: _without_server_managed_fields(row)
        for row in _entity_rows(after_body)
        for key in [_entity_identity_key(row)]
        if key
    }
    shared_keys = set(before_rows).intersection(after_rows)
    if shared_keys:
        return sum(
            1
            for key in shared_keys
            if _canonical(before_rows[key]) != _canonical(after_rows[key])
        )
    before_clean = _without_server_managed_fields(before_body)
    after_clean = _without_server_managed_fields(after_body)
    if (
        not before_rows
        and not after_rows
        and isinstance(before_clean, (dict, list))
        and isinstance(after_clean, (dict, list))
        and _canonical(before_clean) != _canonical(after_clean)
    ):
        return 1
    return 0


def observe_authorization_comparison(
    *,
    control: dict[str, Any],
    treatment: dict[str, Any],
    require_same_resource: bool,
    business_effect: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare authorized and restricted observations without status-only proof."""

    control_row = _dict(control)
    treatment_row = _dict(treatment)
    base_evidence: dict[str, Any] = {
        "control_status": _response_status(control_row),
        "treatment_status": _response_status(treatment_row),
        "control_body_fingerprint": _fingerprint(control_row.get("body")),
        "treatment_body_fingerprint": _fingerprint(treatment_row.get("body")),
        "same_resource_required": bool(require_same_resource),
        "same_resource_proven": False,
        "owner_can_access": None,
        "viewer_can_access": None,
        "leak_detected": None,
    }
    if not control_row or not _is_success(control_row):
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="AUTHORIZED_CONTROL_FAILED",
            evidence=base_evidence,
        )
    if _text(control_row.get("method")).upper() != "GET":
        effect = _dict(business_effect)
        control_effect = effect.get("control_effect_count")
        treatment_effect = effect.get("treatment_effect_count")
        base_evidence.update({
            "control_effect_count": control_effect,
            "treatment_effect_count": treatment_effect,
        })
        if effect.get("business_effect_observed") is not True or control_effect is None:
            return _receipt(
                observer_id="authorization_comparison",
                status="INDETERMINATE",
                reason_code="WRITE_EFFECT_EVIDENCE_REQUIRED",
                evidence=base_evidence,
            )
        if int(control_effect) <= 0:
            return _receipt(
                observer_id="authorization_comparison",
                status="INDETERMINATE",
                reason_code="AUTHORIZED_CONTROL_EFFECT_MISSING",
                evidence=base_evidence,
            )
        if treatment_effect is not None and int(treatment_effect) > 0:
            base_evidence.update({
                "same_resource_proven": True,
                "resource_match_basis": "source_grounded_business_effect",
                "owner_can_access": True,
                "viewer_can_access": True,
                "leak_detected": True,
            })
            return _receipt(
                observer_id="authorization_comparison",
                status="OBSERVED",
                evidence=base_evidence,
            )
        if _response_status(treatment_row) in {401, 403, 404} and int(treatment_effect or 0) == 0:
            control_path = _text(control_row.get("path")).split("?", 1)[0]
            treatment_path = _text(treatment_row.get("path")).split("?", 1)[0]
            same_path = bool(control_path and control_path == treatment_path)
            base_evidence.update({
                "same_resource_proven": same_path,
                "resource_match_basis": (
                    "same_requested_resource_path_and_explicit_deny"
                    if same_path
                    else "explicit_deny_different_resource"
                ),
                "owner_can_access": True,
                "viewer_can_access": False,
                "leak_detected": False,
            })
            return _receipt(
                observer_id="authorization_comparison",
                status="OBSERVED",
                evidence=base_evidence,
            )
        if _is_success(treatment_row) and int(treatment_effect or 0) == 0:
            base_evidence.update({
                "same_resource_proven": True,
                "resource_match_basis": "source_grounded_business_effect_absence",
                "owner_can_access": True,
                "viewer_can_access": False,
                "viewer_request_accepted": True,
                "viewer_business_effect_observed": False,
                "leak_detected": False,
            })
            return _receipt(
                observer_id="authorization_comparison",
                status="OBSERVED",
                evidence=base_evidence,
            )
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="WRITE_EFFECT_EVIDENCE_REQUIRED",
            evidence=base_evidence,
        )
    if not _meaningful_resource_body(control_row):
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="CONTROL_RESOURCE_EVIDENCE_MISSING",
            evidence=base_evidence,
        )
    base_evidence["owner_can_access"] = True

    treatment_status = _response_status(treatment_row)
    if treatment_status in {401, 403, 404}:
        same_path = (
            _text(control_row.get("path")).split("?", 1)[0]
            == _text(treatment_row.get("path")).split("?", 1)[0]
        )
        if require_same_resource and not same_path:
            return _receipt(
                observer_id="authorization_comparison",
                status="INDETERMINATE",
                reason_code="SAME_RESOURCE_NOT_PROVEN",
                evidence=base_evidence,
            )
        base_evidence.update({
            "same_resource_proven": same_path,
            "resource_match_basis": "same_requested_resource_path",
            "viewer_can_access": False,
            "leak_detected": False,
        })
        return _receipt(
            observer_id="authorization_comparison",
            status="OBSERVED",
            evidence=base_evidence,
        )
    if not _is_success(treatment_row):
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="TREATMENT_REJECTION_AMBIGUOUS",
            evidence=base_evidence,
        )
    if not _meaningful_resource_body(treatment_row):
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="TREATMENT_RESOURCE_EVIDENCE_MISSING",
            evidence=base_evidence,
        )

    same_path = (
        _text(control_row.get("path")).split("?", 1)[0]
        == _text(treatment_row.get("path")).split("?", 1)[0]
    )
    bodies_equal = _canonical(control_row.get("body")) == _canonical(treatment_row.get("body"))
    identity_overlap = bool(
        _identity_fingerprints(control_row.get("body"))
        .intersection(_identity_fingerprints(treatment_row.get("body")))
    )
    # Equal payloads alone are not resource identity: a shared login/error
    # document can be byte-identical for both actors. Require a structural
    # identity value from the observed JSON on the same requested path.
    same_resource_proven = same_path and identity_overlap
    if require_same_resource and not same_resource_proven:
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="SAME_RESOURCE_NOT_PROVEN",
            evidence=base_evidence,
        )

    base_evidence.update({
        "same_resource_proven": same_resource_proven or not require_same_resource,
        "resource_match_basis": (
            "identity_overlap"
            if identity_overlap
            else "canonical_body_match_without_identity"
            if bodies_equal
            else "not_required"
        ),
        "viewer_can_access": True,
        "leak_detected": True,
    })
    return _receipt(
        observer_id="authorization_comparison",
        status="OBSERVED",
        evidence=base_evidence,
    )


def _observe_http_response(
    control: dict[str, Any],
    treatment: dict[str, Any],
) -> dict[str, Any]:
    rows = [row for row in (control, treatment) if row]
    statuses = [_response_status(row) for row in rows]
    if not rows or any(status <= 0 for status in statuses):
        return _receipt(
            observer_id="http_response",
            status="FAILED",
            reason_code="HTTP_RESPONSE_MISSING",
            evidence={"statuses": statuses},
        )
    return _receipt(
        observer_id="http_response",
        status="OBSERVED",
        evidence={
            "statuses": statuses,
            "response_fingerprints": [_fingerprint(row.get("body")) for row in rows],
        },
    )


def _observe_actor_identity(
    control_actor_ref: str,
    treatment_actor_ref: str,
) -> dict[str, Any]:
    actor_refs = [
        value
        for value in dict.fromkeys((_text(control_actor_ref), _text(treatment_actor_ref)))
        if value
    ]
    if not actor_refs:
        return _receipt(
            observer_id="actor_identity",
            status="FAILED",
            reason_code="ACTOR_IDENTITY_MISSING",
            evidence={"actor_ref_fingerprints": []},
        )
    return _receipt(
        observer_id="actor_identity",
        status="OBSERVED",
        evidence={
            "actor_ref_fingerprints": [_fingerprint(value) for value in actor_refs],
            "distinct_actor_count": len(actor_refs),
        },
    )


def _observe_resource_ownership(
    binding_receipts: list[dict[str, Any]],
    *,
    expected_owner_actor_ref: str,
) -> dict[str, Any]:
    proof = next((
        receipt
        for receipt in binding_receipts
        if isinstance(receipt, dict)
        and _text(receipt.get("ownership_proof_status")) == "OBSERVED"
        and _text(receipt.get("owner_actor_ref")) == _text(expected_owner_actor_ref)
        and _text(receipt.get("value_fingerprint"))
    ), None)
    if not isinstance(proof, dict):
        return _receipt(
            observer_id="resource_ownership",
            status="INDETERMINATE",
            reason_code="RESOURCE_OWNERSHIP_PROOF_MISSING",
            evidence={
                "owner_actor_ref_fingerprint": (
                    _fingerprint(expected_owner_actor_ref)
                    if _text(expected_owner_actor_ref)
                    else ""
                ),
                "resource_identity_fingerprint": "",
            },
        )
    return _receipt(
        observer_id="resource_ownership",
        status="OBSERVED",
        evidence={
            "fixture_id": _text(proof.get("fixture_id")),
            "owner_actor_ref_fingerprint": _fingerprint(expected_owner_actor_ref),
            "resource_identity_fingerprint": _text(proof.get("value_fingerprint")),
            "ownership_proof_ref": _text(proof.get("ownership_proof_ref")),
        },
    )


def _effect_window(steps: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    if not steps:
        return {}, "BUSINESS_EFFECT_WRITE_MISSING"
    first_governance = _dict(steps[0].get("governance_receipt"))
    last_governance = _dict(steps[-1].get("governance_receipt"))
    before = _dict(first_governance.get("before"))
    after = _dict(last_governance.get("after"))
    if not (
        200 <= _raw_http_status(before) < 300
        and 200 <= _raw_http_status(after) < 300
    ):
        return {}, "BUSINESS_EFFECT_OBSERVATION_FAILED"
    if not isinstance(before.get("body"), (dict, list)) or not isinstance(after.get("body"), (dict, list)):
        return {}, "BUSINESS_EFFECT_BODY_UNSUPPORTED"
    before_ids = _identity_fingerprints(before.get("body"))
    after_ids = _identity_fingerprints(after.get("body"))
    identity_effect_count = len(after_ids - before_ids)
    business_field_change_count = _business_field_change_count(
        before.get("body"),
        after.get("body"),
    )
    return {
        "before_identity_count": len(before_ids),
        "after_identity_count": len(after_ids),
        "identity_effect_count": identity_effect_count,
        "business_field_change_count": business_field_change_count,
        "effect_count": identity_effect_count + business_field_change_count,
        "before_fingerprint": _fingerprint(before.get("body")),
        "after_fingerprint": _fingerprint(after.get("body")),
    }, ""


def _observe_business_effect(
    execution_steps: list[dict[str, Any]],
    *,
    aggregate_control_treatment: bool = False,
) -> dict[str, Any]:
    write_steps = [
        step
        for step in execution_steps
        if isinstance(step, dict)
        and _text(step.get("phase")) in {"control", "treatment"}
        and _text(step.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and _dict(step.get("governance_receipt"))
    ]
    evidence: dict[str, Any] = {
        "http_statuses": [_response_status(step) for step in write_steps],
        "business_effect_observed": False,
    }
    if not write_steps:
        return _receipt(
            observer_id="business_effect",
            status="INDETERMINATE",
            reason_code="BUSINESS_EFFECT_WRITE_MISSING",
            evidence=evidence,
        )

    phase_windows: dict[str, dict[str, Any]] = {}
    for phase in ("control", "treatment"):
        phase_steps = [step for step in write_steps if _text(step.get("phase")) == phase]
        if not phase_steps:
            continue
        window, reason = _effect_window(phase_steps)
        if reason:
            return _receipt(
                observer_id="business_effect",
                status="INDETERMINATE",
                reason_code=reason,
                evidence=evidence,
            )
        phase_windows[phase] = window
        evidence[f"{phase}_effect_count"] = window["effect_count"]

    combined_window: dict[str, Any] = {}
    if aggregate_control_treatment:
        combined_window, combined_reason = _effect_window(write_steps)
        if combined_reason:
            return _receipt(
                observer_id="business_effect",
                status="INDETERMINATE",
                reason_code=combined_reason,
                evidence=evidence,
            )
        evidence["combined_effect_count"] = combined_window["effect_count"]
    selected = (
        combined_window
        or phase_windows.get("treatment")
        or phase_windows.get("control")
    )
    if not selected:
        return _receipt(
            observer_id="business_effect",
            status="INDETERMINATE",
            reason_code="BUSINESS_EFFECT_WINDOW_MISSING",
            evidence=evidence,
        )
    evidence.update({
        "effect_count": selected["effect_count"],
        "identity_effect_count": selected["identity_effect_count"],
        "business_field_change_count": selected["business_field_change_count"],
        "before_identity_count": selected["before_identity_count"],
        "after_identity_count": selected["after_identity_count"],
        "before_fingerprint": selected["before_fingerprint"],
        "after_fingerprint": selected["after_fingerprint"],
        "business_effect_observed": True,
    })
    return _receipt(
        observer_id="business_effect",
        status="OBSERVED",
        evidence=evidence,
    )


def _conservation_terms_from_experiment(experiment: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for assertion in _list(experiment.get("assertions")):
        if not isinstance(assertion, dict):
            continue
        prop = _dict(assertion.get("property"))
        expression = _dict(assertion.get("expression") or prop.get("expression"))
        equation = _dict(
            assertion.get("equation")
            or prop.get("equation")
            or expression.get("equation")
        )
        kind = _text(
            assertion.get("kind")
            or assertion.get("type")
            or prop.get("kind")
            or expression.get("kind")
        ).lower()
        if kind != "conservation" and not equation:
            continue
        for term in _list(equation.get("terms") or equation.get("fields")):
            if _text(term):
                terms.append(_text(term))
    return list(dict.fromkeys(terms))


def _numeric_snapshot_values(body: Any, terms: list[str]) -> dict[str, int | float]:
    term_lookup = {_normalized_field_name(term): term for term in terms if _text(term)}
    values: dict[str, int | float] = {}
    for row in _entity_rows(body):
        cleaned = _without_server_managed_fields(row)
        if not isinstance(cleaned, dict):
            continue
        for key, child in cleaned.items():
            normalized = _normalized_field_name(key)
            if not normalized or normalized in {"id", "uuid", "key"} or normalized.endswith("_id"):
                continue
            if isinstance(child, bool):
                continue
            if not isinstance(child, (int, float)):
                # Try to parse string numbers (common in JSON APIs)
                if isinstance(child, str):
                    try:
                        child = float(child) if "." in child else int(child)
                    except (ValueError, TypeError):
                        continue
                else:
                    continue
            if term_lookup and normalized not in term_lookup:
                continue
            out_key = term_lookup.get(normalized, normalized)
            values[out_key] = values.get(out_key, 0) + child
    return {
        key: values[key]
        for key in sorted(values)
    }


def _observe_entity_state(
    execution_steps: list[dict[str, Any]],
    *,
    experiment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    write_steps = [
        step
        for step in execution_steps
        if isinstance(step, dict)
        and _text(step.get("phase")) in {"control", "treatment"}
        and _text(step.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and _dict(step.get("governance_receipt"))
    ]
    evidence: dict[str, Any] = {
        "http_statuses": [_response_status(step) for step in write_steps],
        "entity_state_observed": False,
        "state_change_count": 0,
    }
    if not write_steps:
        return _receipt(
            observer_id="entity_state",
            status="INDETERMINATE",
            reason_code="ENTITY_STATE_WRITE_MISSING",
            evidence=evidence,
        )

    state_windows: list[dict[str, Any]] = []
    for step in write_steps:
        window, reason = _effect_window([step])
        if reason:
            return _receipt(
                observer_id="entity_state",
                status="INDETERMINATE",
                reason_code=reason.replace("BUSINESS_EFFECT", "ENTITY_STATE"),
                evidence=evidence,
            )
        state_changed = window["before_fingerprint"] != window["after_fingerprint"]
        state_windows.append({
            "phase": _text(step.get("phase")),
            "step_id": _text(step.get("step_id")),
            "before_identity_count": window["before_identity_count"],
            "after_identity_count": window["after_identity_count"],
            "effect_count": window["effect_count"],
            "before_fingerprint": window["before_fingerprint"],
            "after_fingerprint": window["after_fingerprint"],
            "state_changed": state_changed,
        })

    conservation_terms = _conservation_terms_from_experiment(_dict(experiment))
    first_governance = _dict(write_steps[0].get("governance_receipt"))
    last_governance = _dict(write_steps[-1].get("governance_receipt"))
    before_values = _numeric_snapshot_values(
        _dict(first_governance.get("before")).get("body"),
        conservation_terms,
    )
    after_values = _numeric_snapshot_values(
        _dict(last_governance.get("after")).get("body"),
        conservation_terms,
    )
    if before_values or after_values:
        evidence.update({
            "conservation_terms": conservation_terms,
            "before_values": before_values,
            "after_values": after_values,
        })
    elif conservation_terms:
        # Terms specified but not found in snapshots
        return _receipt(
            observer_id="entity_state",
            status="INDETERMINATE",
            reason_code="CONSERVATION_VALUES_MISSING",
            evidence=evidence,
        )
    else:
        # No terms specified and no values extracted — still provide empty dicts
        evidence.update({
            "conservation_terms": [],
            "before_values": {},
            "after_values": {},
        })

    evidence.update({
        "entity_state_observed": True,
        "state_change_count": sum(1 for row in state_windows if row["state_changed"]),
        "effect_count": sum(int(row["effect_count"]) for row in state_windows),
        "state_windows": state_windows,
    })
    return _receipt(
        observer_id="entity_state",
        status="OBSERVED",
        evidence=evidence,
    )


_STATE_FIELD_NAMES = {
    "state",
    "status",
    "stage",
    "phase",
    "lifecycle_state",
    "workflow_state",
    "order_state",
    "order_status",
    "payment_state",
    "payment_status",
    "resource_state",
    "resource_status",
}


def _normalized_field_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(value).lower()).strip("_")


def _state_field_priority(field_name: str) -> int:
    normalized = _normalized_field_name(field_name)
    if normalized in _STATE_FIELD_NAMES:
        return 0
    if normalized.endswith("_state") or normalized.endswith("_status"):
        return 1
    return 2


def _extract_state_value(value: Any) -> tuple[str, str]:
    candidates: list[tuple[int, str, str]] = []
    for row in _entity_rows(value):
        for key, child in row.items():
            normalized_key = _normalized_field_name(key)
            if normalized_key not in _STATE_FIELD_NAMES and not (
                normalized_key.endswith("_state")
                or normalized_key.endswith("_status")
            ):
                continue
            if isinstance(child, (str, int, float, bool)) and _text(child):
                candidates.append((_state_field_priority(str(key)), str(key), _text(child)))
    if not candidates:
        return "", ""
    selected = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
    same_field_values = {
        value
        for priority, field, value in candidates
        if priority == selected[0] and field == selected[1]
    }
    if len(same_field_values) != 1:
        return "", selected[1]
    return selected[2], selected[1]


def _main_governed_write_steps(execution_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        step
        for step in execution_steps
        if isinstance(step, dict)
        and _text(step.get("phase")) in {"control", "treatment"}
        and _text(step.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and _dict(step.get("governance_receipt"))
    ]


def _state_snapshot_evidence(
    step: dict[str, Any],
    *,
    snapshot_key: str,
    state_key: str,
) -> tuple[dict[str, Any], str]:
    governance = _dict(step.get("governance_receipt"))
    snapshot = _dict(governance.get(snapshot_key))
    evidence: dict[str, Any] = {
        "snapshot_phase": snapshot_key,
        "source_step_id": _text(step.get("step_id")),
        "source_phase": _text(step.get("phase")),
        "source_operation_ref": _text(step.get("operation_ref")),
        "snapshot_http_status": _raw_http_status(snapshot),
        "snapshot_body_fingerprint": _fingerprint(snapshot.get("body")),
        "snapshot_identity_count": len(_identity_fingerprints(snapshot.get("body"))),
    }
    if not (200 <= _raw_http_status(snapshot) < 300):
        return evidence, "STATE_SNAPSHOT_OBSERVATION_FAILED"
    if not isinstance(snapshot.get("body"), (dict, list)):
        return evidence, "STATE_SNAPSHOT_BODY_UNSUPPORTED"
    state_value, state_field = _extract_state_value(snapshot.get("body"))
    evidence["state_field"] = state_field
    evidence[state_key] = state_value
    if not state_value:
        return evidence, "STATE_SNAPSHOT_VALUE_MISSING"
    return evidence, ""


def _observe_state_snapshot(
    observer_id: str,
    execution_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    main_steps = _main_governed_write_steps(execution_steps)
    if not main_steps:
        return _receipt(
            observer_id=observer_id,
            status="INDETERMINATE",
            reason_code="STATE_SNAPSHOT_WRITE_MISSING",
            evidence={"write_step_count": 0},
        )
    if observer_id == "before_state":
        step = main_steps[0]
        snapshot_key = "before"
        state_key = "before_state"
    elif observer_id == "after_state":
        step = main_steps[-1]
        snapshot_key = "after"
        state_key = "after_state"
    elif observer_id == "final_state":
        step = main_steps[-1]
        snapshot_key = "after"
        state_key = "final_state"
    else:
        raise ValueError(f"state_snapshot_observer_invalid:{observer_id}")
    evidence, reason = _state_snapshot_evidence(
        step,
        snapshot_key=snapshot_key,
        state_key=state_key,
    )
    evidence["write_step_count"] = len(main_steps)
    evidence["cleanup_phase_excluded"] = True
    if reason:
        return _receipt(
            observer_id=observer_id,
            status="INDETERMINATE",
            reason_code=reason,
            evidence=evidence,
        )
    return _receipt(
        observer_id=observer_id,
        status="OBSERVED",
        evidence=evidence,
    )


def _raw_barrier_events(observations: dict[str, Any]) -> list[Any]:
    for key in ("barrier_timeline", "barrier_events", "concurrency_timeline"):
        value = observations.get(key)
        if value is not None:
            return _list(value)
    return []


def _event_time_ms(event: dict[str, Any], index: int) -> int:
    for key in ("at_ms", "timestamp_ms", "offset_ms", "elapsed_ms"):
        value = event.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return index
    return index


def _normalize_barrier_timeline(events: list[Any]) -> tuple[list[dict[str, Any]], str]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(events):
        if not isinstance(raw, dict):
            return [], "BARRIER_EVENT_INVALID"
        event_name = _text(raw.get("event") or raw.get("phase") or raw.get("kind")).lower()
        participant = _text(
            raw.get("participant")
            or raw.get("slot")
            or raw.get("actor_ref")
            or raw.get("request_id")
        )
        if not event_name:
            return [], "BARRIER_EVENT_INVALID"
        normalized.append({
            "event": event_name,
            "participant": participant,
            "at_ms": _event_time_ms(raw, index),
        })
    if not normalized:
        return [], "BARRIER_TIMELINE_MISSING"
    return sorted(normalized, key=lambda item: (int(item["at_ms"]), item["event"], item["participant"])), ""


def _observe_barrier_timeline(observations: dict[str, Any]) -> dict[str, Any]:
    raw_events = _raw_barrier_events(observations)
    events, reason = _normalize_barrier_timeline(raw_events)
    if reason:
        return _receipt(
            observer_id="barrier_timeline",
            status="INDETERMINATE",
            reason_code=reason,
            evidence={"event_count": 0},
        )

    release_events = [
        event
        for event in events
        if event["event"] in {"release", "barrier_release", "start", "released"}
    ]
    participants = sorted({
        event["participant"]
        for event in events
        if event["participant"] and event["participant"].lower() != "all"
    })
    evidence = {
        "event_count": len(events),
        "release_event_count": len(release_events),
        "participant_count": len(participants),
        "barrier_released": bool(release_events),
        "timeline_fingerprint": _fingerprint(events),
        "participants_fingerprint": _fingerprint(participants),
        "first_event": dict(events[0]),
        "last_event": dict(events[-1]),
    }
    if not release_events:
        return _receipt(
            observer_id="barrier_timeline",
            status="INDETERMINATE",
            reason_code="BARRIER_RELEASE_MISSING",
            evidence=evidence,
        )
    if len(participants) < 2:
        return _receipt(
            observer_id="barrier_timeline",
            status="INDETERMINATE",
            reason_code="BARRIER_PARTICIPANT_COUNT_INSUFFICIENT",
            evidence=evidence,
        )
    return _receipt(
        observer_id="barrier_timeline",
        status="OBSERVED",
        evidence=evidence,
    )


def _observe_temporal_window(
    observations: dict[str, Any],
    *,
    assertion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe convergence only from explicit, source-bounded timeline facts."""

    timeline = _list(observations.get("temporal_timeline"))
    evidence: dict[str, Any] = {
        "timeline_event_count": len(timeline),
        "converged": False,
        "within_window": None,
        "elapsed_ms": None,
        "window_ms": None,
    }
    if not timeline:
        return _receipt(
            observer_id="temporal_window",
            status="INDETERMINATE",
            reason_code="TEMPORAL_TIMELINE_MISSING",
            evidence=evidence,
        )

    assertion_row = _dict(assertion)
    prop = _dict(assertion_row.get("property"))
    window_ms: int | None = None
    for key in ("window_ms", "max_ms", "timeout_ms", "convergence_window_ms"):
        raw = prop.get(key) if prop.get(key) is not None else assertion_row.get(key)
        if raw is None:
            continue
        try:
            window_ms = int(raw)
        except (TypeError, ValueError):
            return _receipt(
                observer_id="temporal_window",
                status="FAILED",
                reason_code="TEMPORAL_WINDOW_INVALID",
                evidence=evidence,
            )
        break
    if window_ms is None or window_ms <= 0:
        return _receipt(
            observer_id="temporal_window",
            status="INDETERMINATE",
            reason_code="TEMPORAL_WINDOW_NOT_SPECIFIED",
            evidence=evidence,
        )
    evidence["window_ms"] = window_ms

    trigger_events = [
        row for row in timeline
        if isinstance(row, dict) and _text(row.get("event")) == "trigger"
    ]
    final_events = [
        row for row in timeline
        if isinstance(row, dict) and _text(row.get("event")) == "final_observed"
    ]
    if not trigger_events:
        return _receipt(
            observer_id="temporal_window",
            status="INDETERMINATE",
            reason_code="TEMPORAL_TRIGGER_EVENT_MISSING",
            evidence=evidence,
        )
    if not final_events:
        return _receipt(
            observer_id="temporal_window",
            status="INDETERMINATE",
            reason_code="TEMPORAL_FINAL_EVENT_MISSING",
            evidence=evidence,
        )
    try:
        trigger_at = min(int(_dict(row).get("at_ms")) for row in trigger_events)
        final_at = max(int(_dict(row).get("at_ms")) for row in final_events)
    except (TypeError, ValueError):
        return _receipt(
            observer_id="temporal_window",
            status="FAILED",
            reason_code="TEMPORAL_TIMELINE_INVALID",
            evidence=evidence,
        )
    if final_at < trigger_at:
        return _receipt(
            observer_id="temporal_window",
            status="FAILED",
            reason_code="TEMPORAL_TIMELINE_ORDER_INVALID",
            evidence=evidence,
        )

    elapsed_ms = final_at - trigger_at
    evidence.update({
        "elapsed_ms": elapsed_ms,
        "trigger_at_ms": trigger_at,
        "final_at_ms": final_at,
        "converged": True,
        "within_window": elapsed_ms <= window_ms,
    })
    return _receipt(
        observer_id="temporal_window",
        status="OBSERVED",
        evidence=evidence,
    )


_TYPED_ASSERTION_KINDS = {
    "authorization",
    "cardinality",
    "concurrency",
    "concurrency_final_invariant",
    "conservation",
    "cross_surface_consistency",
    "delta",
    "equality",
    "eventual_consistency",
    "http_status",
    "http_status_class",
    "idempotency",
    "idempotency_effect",
    "isolation",
    "json_path_compare",
    "json_path_exists",
    "json_path_type",
    "owner_tenant_visibility",
    "privacy",
    "state",
    "state_transition",
    "temporal",
    "validation",
    "visibility",
}


def _primary_assertion(experiment: dict[str, Any]) -> dict[str, Any]:
    return _dict(
        _list(_dict(experiment).get("assertions"))[0]
        if _list(_dict(experiment).get("assertions"))
        else {}
    )


def _observe_typed_assertion(experiment: dict[str, Any]) -> dict[str, Any]:
    assertion = _primary_assertion(experiment)
    kind = _text(assertion.get("kind") or assertion.get("type"))
    assertion_id = _text(assertion.get("assertion_id") or assertion.get("id"))
    evidence = {
        "assertion_id": assertion_id,
        "assertion_kind": kind,
        "typed_assertion": False,
        "property_fingerprint": _fingerprint(_dict(assertion.get("property"))),
        "expected_from": _text(assertion.get("expected_from")),
    }
    if not assertion:
        return _receipt(
            observer_id="typed_assertion",
            status="INDETERMINATE",
            reason_code="TYPED_ASSERTION_MISSING",
            evidence=evidence,
        )
    if kind not in _TYPED_ASSERTION_KINDS:
        return _receipt(
            observer_id="typed_assertion",
            status="INDETERMINATE",
            reason_code="TYPED_ASSERTION_UNSUPPORTED",
            evidence=evidence,
        )
    evidence["typed_assertion"] = True
    return _receipt(
        observer_id="typed_assertion",
        status="OBSERVED",
        evidence=evidence,
    )


def _source_refs_from_experiment(experiment: dict[str, Any], assertion: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        dict(item)
        for item in _list(_dict(experiment).get("source_refs"))
        if isinstance(item, dict)
    ]
    refs.extend(
        dict(item)
        for item in _list(_dict(assertion).get("source_refs"))
        if isinstance(item, dict)
    )
    deduped: dict[str, dict[str, Any]] = {}
    for ref in refs:
        key = _fingerprint(ref)
        deduped.setdefault(key, ref)
    return list(deduped.values())


def _observe_source_invariant(experiment: dict[str, Any]) -> dict[str, Any]:
    assertion = _primary_assertion(experiment)
    prop = _dict(assertion.get("property"))
    invariant_ref = _text(
        prop.get("invariant_ref")
        or assertion.get("invariant_ref")
        or prop.get("rule_ref")
        or assertion.get("rule_ref")
    )
    expression = prop.get("expression") or assertion.get("expression")
    source_refs = _source_refs_from_experiment(experiment, assertion)
    evidence = {
        "invariant_ref": invariant_ref,
        "expression_fingerprint": _fingerprint(expression),
        "source_ref_count": len(source_refs),
        "source_ref_fingerprints": [_fingerprint(ref) for ref in source_refs],
    }
    if not invariant_ref and not expression:
        return _receipt(
            observer_id="source_invariant",
            status="INDETERMINATE",
            reason_code="SOURCE_INVARIANT_REF_MISSING",
            evidence=evidence,
        )
    if not source_refs:
        return _receipt(
            observer_id="source_invariant",
            status="INDETERMINATE",
            reason_code="SOURCE_INVARIANT_SOURCE_REF_MISSING",
            evidence=evidence,
        )
    return _receipt(
        observer_id="source_invariant",
        status="OBSERVED",
        evidence=evidence,
    )


def observe_experiment_requirements(
    experiment: dict[str, Any],
    *,
    observations: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> list[dict[str, Any]]:
    """Emit one typed receipt for every observer declared by the experiment."""

    exp = _dict(experiment)
    resolved_campaign_id = _text(campaign_id or exp.get("campaign_id"))
    resolved_execution_id = _text(execution_id or exp.get("execution_id"))
    if bool(resolved_campaign_id) != bool(resolved_execution_id):
        raise ValueError("observer_receipt_lineage_incomplete")
    evidence = _dict(observations)
    control = _dict(evidence.get("control_observation"))
    treatment = _dict(evidence.get("treatment_observation"))
    assertion = _dict(_list(exp.get("assertions"))[0] if _list(exp.get("assertions")) else {})
    prop = _dict(assertion.get("property"))
    receipts: list[dict[str, Any]] = []
    observer_rows = [
        observer
        for observer in _list(exp.get("observers"))
        if isinstance(observer, dict)
    ]
    business_effect_receipt: dict[str, Any] = {}
    if any(_text(observer.get("observer_id")) == "business_effect" for observer in observer_rows):
        business_effect_receipt = _observe_business_effect(
            [
                step
                for step in _list(evidence.get("execution_steps"))
                if isinstance(step, dict)
            ],
            aggregate_control_treatment=(
                _text(assertion.get("kind") or assertion.get("type"))
                in {"idempotency", "idempotency_effect"}
            ),
        )
    business_effect_evidence = (
        _dict(business_effect_receipt.get("evidence"))
        if _text(business_effect_receipt.get("status")) == "OBSERVED"
        else {}
    )
    for observer in observer_rows:
        observer_id = _text(_dict(observer).get("observer_id"))
        if observer_id == "http_response":
            receipt = _observe_http_response(control, treatment)
        elif observer_id == "actor_identity":
            receipt = _observe_actor_identity(
                _text(evidence.get("control_actor_ref")),
                _text(evidence.get("treatment_actor_ref")),
            )
        elif observer_id == "resource_ownership":
            receipt = _observe_resource_ownership(
                [
                    row
                    for row in _list(evidence.get("binding_materialization_receipts"))
                    if isinstance(row, dict)
                ],
                expected_owner_actor_ref=_text(
                    prop.get("owner_actor_ref") or prop.get("control_actor_ref")
                ),
            )
        elif observer_id == "authorization_comparison":
            receipt = observe_authorization_comparison(
                control=control,
                treatment=treatment,
                require_same_resource=bool(prop.get("require_same_resource", True)),
                business_effect=business_effect_evidence,
            )
        elif observer_id == "business_effect":
            receipt = business_effect_receipt
        elif observer_id == "entity_state":
            receipt = _observe_entity_state(
                [
                    step
                    for step in _list(evidence.get("execution_steps"))
                    if isinstance(step, dict)
                ],
                experiment=exp,
            )
        elif observer_id in {"before_state", "after_state", "final_state"}:
            receipt = _observe_state_snapshot(
                observer_id,
                [
                    step
                    for step in _list(evidence.get("execution_steps"))
                    if isinstance(step, dict)
                ],
            )
        elif observer_id == "barrier_timeline":
            receipt = _observe_barrier_timeline(evidence)
        elif observer_id == "temporal_window":
            receipt = _observe_temporal_window(evidence, assertion=assertion)
        elif observer_id == "typed_assertion":
            receipt = _observe_typed_assertion(exp)
        elif observer_id == "source_invariant":
            receipt = _observe_source_invariant(exp)
        else:
            receipt = _receipt(
                observer_id=observer_id,
                status="UNSUPPORTED",
                reason_code="OBSERVER_IMPLEMENTATION_MISSING",
                evidence={},
            )
        if resolved_campaign_id:
            receipt = bind_observer_receipt_lineage(
                receipt,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
            )
        receipts.append(receipt)
    return receipts


# ── Authorization override (merged from observer_contracts.py) ──────────

_original_authorization_comparison = observe_authorization_comparison


def _dict_obs(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_obs(value: Any) -> str:
    return str(value or "").strip()


def _status_obs(value: Any) -> int:
    row = _dict_obs(value)
    try:
        return int(row.get("status_code") or row.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def observe_authorization_comparison(
    *,
    control: dict[str, Any],
    treatment: dict[str, Any],
    require_same_resource: bool,
    business_effect: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline = _original_authorization_comparison(
        control=control,
        treatment=treatment,
        require_same_resource=require_same_resource,
        business_effect=business_effect,
    )
    control_row = _dict_obs(control)
    treatment_row = _dict_obs(treatment)
    if _text_obs(control_row.get("method")).upper() == "GET":
        return baseline

    effect = _dict_obs(business_effect)
    control_effect = effect.get("control_effect_count")
    treatment_effect = effect.get("treatment_effect_count")
    try:
        proven_control = (
            effect.get("business_effect_observed") is True
            and control_effect is not None
            and int(control_effect) > 0
        )
        proven_treatment_effect = (
            treatment_effect is not None
            and int(treatment_effect) > 0
        )
    except (TypeError, ValueError):
        return baseline
    if not (
        proven_control
        and proven_treatment_effect
        and 200 <= _status_obs(treatment_row) < 300
    ):
        return baseline

    control_path = _text_obs(control_row.get("path")).split("?", 1)[0]
    treatment_path = _text_obs(treatment_row.get("path")).split("?", 1)[0]
    same_path = bool(control_path and control_path == treatment_path)
    evidence = dict(_dict_obs(baseline.get("evidence")))
    evidence.update({
        "control_effect_count": int(control_effect),
        "treatment_effect_count": int(treatment_effect),
        "viewer_request_accepted": True,
        "viewer_business_effect_observed": True,
    })
    if require_same_resource and not same_path:
        evidence.update({
            "same_resource_proven": False,
            "resource_match_basis": "requested_resource_path_mismatch",
            "owner_can_access": True,
            "viewer_can_access": None,
            "leak_detected": None,
        })
        return _receipt(
            observer_id="authorization_comparison",
            status="INDETERMINATE",
            reason_code="SAME_RESOURCE_NOT_PROVEN",
            evidence=evidence,
        )

    evidence.update({
        "same_resource_proven": same_path or not require_same_resource,
        "resource_match_basis": (
            "same_requested_resource_path_and_explicit_deny"
            if same_path
            else "explicit_deny_operation_scope"
        ),
        "owner_can_access": True,
        "viewer_can_access": True,
        "leak_detected": True,
        "authorization_failure_mode": "restricted_write_request_accepted",
    })
    return _receipt(
        observer_id="authorization_comparison",
        status="OBSERVED",
        evidence=evidence,
    )
