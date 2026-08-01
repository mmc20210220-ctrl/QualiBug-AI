"""Authorization extension for the existing formal customer Delivery Gate.

The v2 Delivery Gate remains the single general adjudication authority. This module
adds the authorization-specific evidence contract that the generic gate cannot infer:
a DELIVERABLE authorization occurrence must carry the complete content-addressed
causality receipt, its exact resource-binding proofs, and a reproduced control /
treatment pair that addressed the same materialized operation and path.

The extension never re-evaluates the business assertion and never creates a positive
finding. It only validates evidence already sealed into the finding payload by Gate v2.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .authorization_oracle_causality import (
    SCHEMA_VERSION,
    authorization_resource_identity_proof_targets,
    build_authorization_observer_binding_proofs,
)
from .customer_delivery_gate_v2 import validate_reproduction_receipt


_AUTHORIZATION_FAMILIES = frozenset({"authorization", "isolation", "visibility"})
_COMPARISON_DIMENSIONS = frozenset({
    "ROLE_PERMISSION",
    "OWNERSHIP_RELATION",
    "TENANT_SCOPE",
})
_CAUSAL_FULL_FIELDS = {
    "schema_version",
    "receipt_id",
    "status",
    "experiment_id",
    "obligation_id",
    "campaign_id",
    "execution_id",
    "reason_codes",
    "comparison_dimension",
    "comparison_contract_fingerprint",
    "compile_binding_graph_fingerprint",
    "runtime_resource_identity_fingerprint",
    "control_target_reached",
    "treatment_target_reached",
    "single_identity_dimension_proven",
    "same_resource_proven",
    "verified_receipt_ids",
}
_CAUSAL_NOT_APPLICABLE_FIELDS = {
    "schema_version",
    "receipt_id",
    "status",
    "experiment_id",
    "obligation_id",
    "campaign_id",
    "execution_id",
    "reason_codes",
    "comparison_contract_fingerprint",
    "runtime_resource_identity_fingerprint",
    "verified_receipt_ids",
}
_BINDING_PROOF_FIELDS = {
    "receipt_id",
    "target",
    "status",
    "value_fingerprint",
}


class AuthorizationDeliveryGateError(ValueError):
    """Authorization delivery evidence is absent, foreign, or contradictory."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _resource_route_path(value: Any) -> str:
    """Return the source route identity without request-query serialization.

    Authorization comparisons may vary one source-declared identity selector in
    the query (for example an ownership filter).  The comparison observer uses
    the route path as the same-resource authority, while the query mutation is
    separately constrained by the compiled comparison contract and causal
    receipt.  Reproduction validation must use that same route identity or a
    valid comparison would be rejected merely because its permitted selector
    was serialized into the URL.
    """

    return _text(value).split("?", 1)[0]


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    token = _text(value).lower()
    return len(token) == 64 and all(char in "0123456789abcdef" for char in token)


def validate_authorization_causality_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Validate the content address and strict semantics of one causal receipt."""
    row = _dict(receipt)
    status = _text(row.get("status")).upper()
    expected_fields = (
        _CAUSAL_NOT_APPLICABLE_FIELDS
        if status == "NOT_APPLICABLE"
        else _CAUSAL_FULL_FIELDS
    )
    if set(row) != expected_fields:
        raise AuthorizationDeliveryGateError(
            "authorization_causality_receipt_fields_invalid"
        )
    if row.get("schema_version") != SCHEMA_VERSION:
        raise AuthorizationDeliveryGateError(
            "authorization_causality_receipt_schema_invalid"
        )
    if status not in {"PASSED", "INDETERMINATE", "NOT_APPLICABLE"}:
        raise AuthorizationDeliveryGateError(
            "authorization_causality_receipt_status_invalid"
        )
    reason_codes = row.get("reason_codes")
    if not isinstance(reason_codes, list):
        raise AuthorizationDeliveryGateError(
            "authorization_causality_reason_codes_invalid"
        )
    canonical_reasons = sorted(
        set(_text(value) for value in reason_codes if _text(value))
    )
    if reason_codes != canonical_reasons:
        raise AuthorizationDeliveryGateError(
            "authorization_causality_reason_codes_not_canonical"
        )
    if not all(
        _text(row.get(field))
        for field in (
            "experiment_id",
            "obligation_id",
            "campaign_id",
            "execution_id",
        )
    ):
        raise AuthorizationDeliveryGateError(
            "authorization_causality_lineage_missing"
        )
    verified = row.get("verified_receipt_ids")
    if (
        not isinstance(verified, list)
        or any(not _text(value) for value in verified)
        or verified != sorted(set(_text(value) for value in verified))
    ):
        raise AuthorizationDeliveryGateError(
            "authorization_causality_verified_receipts_invalid"
        )
    if status == "PASSED":
        if reason_codes:
            raise AuthorizationDeliveryGateError(
                "authorization_causality_passed_with_reasons"
            )
        if _text(row.get("comparison_dimension")) not in _COMPARISON_DIMENSIONS:
            raise AuthorizationDeliveryGateError(
                "authorization_causality_dimension_invalid"
            )
        for field in (
            "comparison_contract_fingerprint",
            "compile_binding_graph_fingerprint",
            "runtime_resource_identity_fingerprint",
        ):
            if not _is_sha256(row.get(field)):
                raise AuthorizationDeliveryGateError(
                    f"authorization_causality_fingerprint_invalid:{field}"
                )
        if not all(
            row.get(field) is True
            for field in (
                "control_target_reached",
                "treatment_target_reached",
                "single_identity_dimension_proven",
                "same_resource_proven",
            )
        ):
            raise AuthorizationDeliveryGateError(
                "authorization_causality_positive_proof_incomplete"
            )
        if not verified:
            raise AuthorizationDeliveryGateError(
                "authorization_causality_verified_receipts_missing"
            )
    elif status == "INDETERMINATE" and not reason_codes:
        raise AuthorizationDeliveryGateError(
            "authorization_causality_indeterminate_reason_missing"
        )

    unsigned = {key: value for key, value in row.items() if key != "receipt_id"}
    expected_id = "auth_causality_" + hashlib.sha256(
        _canonical(unsigned).encode("utf-8")
    ).hexdigest()[:24]
    if _text(row.get("receipt_id")) != expected_id:
        raise AuthorizationDeliveryGateError(
            "authorization_causality_receipt_fingerprint_invalid"
        )
    return deepcopy(row)


def _authorization_required(
    *,
    attempt: dict[str, Any],
    finding: dict[str, Any],
) -> bool:
    if _text(attempt.get("risk_family")).lower() in _AUTHORIZATION_FAMILIES:
        return True
    bundle = _dict(attempt.get("delivery_evidence_bundle"))
    if any(
        _text(_dict(value).get("observer_id")) == "authorization_comparison"
        for value in _list(bundle.get("observer_receipts"))
    ):
        return True
    return bool(
        _dict(finding.get("authorization_causality_receipt"))
        or _text(
            _dict(finding.get("oracle")).get(
                "authorization_causality_receipt_id"
            )
        )
    )


def _binding_proof_fingerprint(
    proofs: list[Any],
) -> tuple[str, set[str]]:
    values: dict[str, str] = {}
    receipt_ids: set[str] = set()
    if not proofs:
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_binding_proofs_missing"
        )
    for raw in proofs:
        row = _dict(raw)
        if set(row) != _BINDING_PROOF_FIELDS:
            raise AuthorizationDeliveryGateError(
                "authorization_delivery_binding_proof_fields_invalid"
            )
        target = _text(row.get("target"))
        receipt_id = _text(row.get("receipt_id"))
        value_fingerprint = _text(row.get("value_fingerprint"))
        if (
            not target
            or not receipt_id
            or _text(row.get("status")).upper() != "BOUND"
            or not value_fingerprint
        ):
            raise AuthorizationDeliveryGateError(
                "authorization_delivery_binding_proof_invalid"
            )
        if target in values:
            raise AuthorizationDeliveryGateError(
                f"authorization_delivery_binding_target_ambiguous:{target}"
            )
        values[target] = value_fingerprint
        receipt_ids.add(receipt_id)
    return _sha256(values), receipt_ids


def _replay_identity_problem(
    reproduction: dict[str, Any],
    *,
    receipt: dict[str, Any],
) -> str:
    replay = validate_reproduction_receipt(_dict(reproduction))
    if _text(replay.get("status")) != "REPRODUCED":
        return "authorization_delivery_reproduction_not_proven"
    for field in (
        "campaign_id",
        "obligation_id",
        "experiment_id",
        "execution_id",
    ):
        if _text(replay.get(field)) != _text(receipt.get(field)):
            return f"authorization_delivery_reproduction_lineage_mismatch:{field}"
    controls = [
        _dict(value)
        for value in _list(replay.get("step_observations"))
        if _text(_dict(value).get("phase")) == "control"
    ]
    treatments = [
        _dict(value)
        for value in _list(replay.get("step_observations"))
        if _text(_dict(value).get("phase")) == "treatment"
    ]
    if len(controls) != 1 or len(treatments) != 1:
        return "authorization_delivery_replay_pair_shape_invalid"
    control = controls[0]
    treatment = treatments[0]
    control_identity = (
        _text(control.get("operation_ref")),
        _text(control.get("method")).upper(),
        _resource_route_path(control.get("path")),
    )
    treatment_identity = (
        _text(treatment.get("operation_ref")),
        _text(treatment.get("method")).upper(),
        _resource_route_path(treatment.get("path")),
    )
    if not all(control_identity) or control_identity != treatment_identity:
        return "authorization_delivery_replay_resource_identity_mismatch"
    return ""


def validate_authorization_delivery_finding(
    finding: dict[str, Any],
    *,
    attempt: dict[str, Any],
    campaign_id: str,
) -> dict[str, Any]:
    """Require a sealed causal receipt before one authorization occurrence publishes."""
    finding_row = _dict(finding)
    attempt_row = _dict(attempt)
    if not _authorization_required(attempt=attempt_row, finding=finding_row):
        return {"status": "NOT_APPLICABLE", "receipt_id": ""}
    if not finding_row:
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_finding_missing"
        )

    receipt = validate_authorization_causality_receipt(
        _dict(finding_row.get("authorization_causality_receipt"))
    )
    if _text(receipt.get("status")) != "PASSED":
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_causality_not_passed"
        )
    expected_lineage = {
        "campaign_id": _text(campaign_id),
        "obligation_id": _text(
            attempt_row.get("executed_obligation_id")
            or finding_row.get("obligation_id")
        ),
        "experiment_id": _text(attempt_row.get("experiment_id")),
        "execution_id": _text(attempt_row.get("execution_id")),
    }
    for field, expected in expected_lineage.items():
        if not expected or _text(receipt.get(field)) != expected:
            raise AuthorizationDeliveryGateError(
                f"authorization_delivery_lineage_mismatch:{field}"
            )
        if _text(finding_row.get(field)) != expected:
            raise AuthorizationDeliveryGateError(
                f"authorization_delivery_finding_lineage_mismatch:{field}"
            )

    receipt_id = _text(receipt.get("receipt_id"))
    oracle = _dict(finding_row.get("oracle"))
    evidence = _dict(finding_row.get("evidence"))
    if (
        _text(oracle.get("authorization_causality_receipt_id")) != receipt_id
        or _text(evidence.get("authorization_causality_receipt_id")) != receipt_id
    ):
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_causality_reference_mismatch"
        )
    if oracle.get("authorization_causality_proven") is not True:
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_causality_flag_missing"
        )
    if (
        _text(evidence.get("runtime_resource_identity_fingerprint"))
        != _text(receipt.get("runtime_resource_identity_fingerprint"))
        or evidence.get("single_identity_dimension_proven") is not True
        or evidence.get("same_resource_proven") is not True
    ):
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_finding_evidence_mismatch"
        )

    _causal_fingerprint = _text(receipt.get("runtime_resource_identity_fingerprint"))
    binding_fingerprint, binding_receipt_ids = _binding_proof_fingerprint(
        _list(finding_row.get("authorization_causality_binding_proofs"))
    )
    if binding_fingerprint != _causal_fingerprint:
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_binding_fingerprint_mismatch"
        )

    bundle = _dict(attempt_row.get("delivery_evidence_bundle"))
    contract_ids = {
        _text(_dict(value).get("receipt_id"))
        for value in _list(bundle.get("contract_evidence_receipts"))
        if _text(_dict(value).get("kind")) in {"control", "treatment"}
        and _text(_dict(value).get("receipt_id"))
    }
    authorization_observer_ids = {
        _text(_dict(value).get("receipt_id"))
        for value in _list(bundle.get("observer_receipts"))
        if _text(_dict(value).get("observer_id")) == "authorization_comparison"
        and _text(_dict(value).get("receipt_id"))
    }
    if len(contract_ids) != 2 or len(authorization_observer_ids) != 1:
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_causal_bundle_shape_invalid"
        )
    required_receipt_ids = (
        contract_ids | authorization_observer_ids | binding_receipt_ids
    )
    verified_receipt_ids = {
        _text(value)
        for value in _list(receipt.get("verified_receipt_ids"))
        if _text(value)
    }
    if not required_receipt_ids.issubset(verified_receipt_ids):
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_verified_receipt_set_incomplete"
        )

    replay_problem = _replay_identity_problem(
        _dict(bundle.get("reproduction_receipt")),
        receipt=receipt,
    )
    if replay_problem:
        raise AuthorizationDeliveryGateError(replay_problem)
    return receipt


def attach_authorization_delivery_evidence(
    result: dict[str, Any],
    *,
    experiment: dict[str, Any],
) -> dict[str, Any]:
    """Embed the causal receipt and exact binding proofs before Gate-v2 seals finding."""
    output = deepcopy(_dict(result))
    finding = _dict(output.get("finding"))
    receipt = _dict(output.get("authorization_causality_receipt"))
    contract = _dict(_dict(experiment).get("authorization_comparison_contract"))
    if not finding or not receipt or not contract:
        return output
    validated = validate_authorization_causality_receipt(receipt)
    if _text(validated.get("status")) != "PASSED":
        return output

    targets = set(authorization_resource_identity_proof_targets(contract))
    proofs: list[dict[str, str]] = []
    observer = next(
        (
            _dict(row)
            for row in _list(output.get("observer_receipts"))
            if _text(_dict(row).get("observer_id"))
            == "authorization_comparison"
        ),
        {},
    )
    observer_fingerprint = ""
    observer_proofs: list[dict[str, str]] = []
    if observer and targets:
        try:
            observer_fingerprint, observer_proofs = (
                build_authorization_observer_binding_proofs(
                    observer,
                    targets,
                )
            )
        except (TypeError, ValueError):
            observer_fingerprint = ""
            observer_proofs = []
    if observer_fingerprint == _text(
        validated.get("runtime_resource_identity_fingerprint")
    ):
        proofs = observer_proofs
    else:
        for raw in _list(output.get("binding_materialization_receipts")):
            row = _dict(raw)
            target = _text(row.get("target") or row.get("binding_target"))
            if target not in targets:
                continue
            proofs.append({
                "receipt_id": _text(
                    row.get("receipt_id") or row.get("materialization_receipt_id")
                ),
                "target": target,
                "status": _text(row.get("status")).upper(),
                "value_fingerprint": _text(row.get("value_fingerprint")),
            })
        proofs.sort(key=lambda value: value["target"])
    finding["authorization_causality_receipt"] = validated
    finding["authorization_causality_binding_proofs"] = proofs
    output["finding"] = finding
    return output


__all__ = [
    "AuthorizationDeliveryGateError",
    "attach_authorization_delivery_evidence",
    "validate_authorization_causality_receipt",
    "validate_authorization_delivery_finding",
]
