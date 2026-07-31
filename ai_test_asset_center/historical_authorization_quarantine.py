"""Receipt-backed quarantine for historical authorization delivery artifacts.

Historical Gate-v1 authorization findings and early Gate-v2 findings that predate
causal receipts cannot be promoted by today's formal delivery authority.  They are
not deleted or rewritten: this module emits a content-addressed quarantine projection
and a minimal rerun queue while preserving the original immutable attempt ledger.

Absence of newer proof is quarantinable.  A present receipt whose fingerprint,
lineage, resource identity, or replay identity contradicts itself remains a hard
error and is never disguised as legacy compatibility.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .authorization_delivery_gate import (
    AuthorizationDeliveryGateError,
    validate_authorization_causality_receipt,
    validate_authorization_delivery_finding,
)
from .binding_materialization_identity_receipt import (
    BindingMaterializationIdentityError,
    validate_binding_materialization_identity_receipt,
)
from .customer_delivery_gate import LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
from .customer_delivery_gate_v2 import CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
from .obligation_attempt_ledger import validate_obligation_attempt_ledger


QUARANTINE_RECEIPT_SCHEMA = (
    "qualibug.historical-authorization-quarantine-receipt.v1"
)
QUARANTINE_PROJECTION_SCHEMA = (
    "qualibug.historical-authorization-quarantine-projection.v1"
)
_AUTHORIZATION_FAMILIES = frozenset({"authorization", "isolation", "visibility"})
_RERUN_REQUIREMENTS = (
    "authorization_comparison_contract_v1",
    "authorization_causality_receipt_v1",
    "binding_materialization_identity_receipt_v1",
    "customer_delivery_gate_v2",
)

# Only missing/old capabilities belong here.  Fingerprint, lineage, resource,
# replay-identity and reference-value mismatches deliberately do not.
_QUARANTINABLE_AUTHORIZATION_CODES = frozenset({
    "authorization_delivery_finding_missing",
    "authorization_causality_receipt_fields_invalid",
    "authorization_causality_receipt_schema_invalid",
    "authorization_delivery_causality_not_passed",
    "authorization_delivery_binding_proofs_missing",
    "authorization_delivery_binding_proof_fields_invalid",
    "authorization_delivery_binding_proof_invalid",
    "authorization_delivery_causality_reference_mismatch",
    "authorization_delivery_causality_flag_missing",
    "authorization_delivery_finding_evidence_mismatch",
    "authorization_delivery_causal_bundle_shape_invalid",
    "authorization_delivery_verified_receipt_set_incomplete",
    "authorization_delivery_reproduction_not_proven",
    "authorization_delivery_replay_pair_shape_invalid",
})


class HistoricalAuthorizationQuarantineError(ValueError):
    """Historical authorization evidence contradicts itself or cannot be sealed."""


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
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _error_code(exc: Exception) -> str:
    return str(exc).split(":", 1)[0]


def authorization_attempt_requires_causal_delivery(
    attempt: dict[str, Any],
) -> bool:
    """Detect authorization semantics from receipt-backed fields, not titles."""
    row = _dict(attempt)
    if _text(row.get("risk_family")).lower() in _AUTHORIZATION_FAMILIES:
        return True
    bundle = _dict(row.get("delivery_evidence_bundle"))
    if any(
        _text(_dict(value).get("observer_id")) == "authorization_comparison"
        for value in _list(bundle.get("observer_receipts"))
    ):
        return True
    finding = _dict(bundle.get("finding"))
    if _text(finding.get("risk_family")).lower() in _AUTHORIZATION_FAMILIES:
        return True
    return bool(
        _dict(finding.get("authorization_causality_receipt"))
        or _text(
            _dict(finding.get("oracle")).get(
                "authorization_causality_receipt_id"
            )
        )
    )


def _quarantine_receipt(
    *,
    attempt: dict[str, Any],
    run_id: str,
    campaign_id: str,
    reason_detail: str,
) -> dict[str, Any]:
    row = _dict(attempt)
    gate = _dict(row.get("gate_receipt"))
    finding_id = _text(row.get("finding_id"))
    obligation_id = _text(
        row.get("executed_obligation_id") or row.get("obligation_id")
    )
    experiment_id = _text(row.get("experiment_id"))
    execution_id = _text(row.get("execution_id"))
    rerun_status = (
        "RERUN_REQUIRED"
        if obligation_id and experiment_id
        else "MANUAL_RECOMPILE_REQUIRED"
    )
    payload = {
        "schema_version": QUARANTINE_RECEIPT_SCHEMA,
        "status": "QUARANTINED",
        "reason_code": "UNVERIFIABLE_LEGACY_AUTHORIZATION",
        "reason_detail": _text(reason_detail),
        "run_id": _text(run_id),
        "campaign_id": _text(campaign_id),
        "finding_id": finding_id,
        "obligation_id": obligation_id,
        "experiment_id": experiment_id,
        "execution_id": execution_id,
        "attempt_fingerprint": _text(row.get("attempt_fingerprint")),
        "gate_schema_version": _text(gate.get("schema_version")),
        "gate_receipt_id": _text(
            gate.get("gate_receipt_id") or gate.get("receipt_id")
        ),
        "rerun_status": rerun_status,
        "rerun_requirements": list(_RERUN_REQUIREMENTS),
    }
    fingerprint = _fingerprint(payload)
    return {
        **payload,
        "receipt_id": "auth_quarantine_" + fingerprint[:24],
        "receipt_fingerprint": fingerprint,
    }


def validate_historical_authorization_quarantine_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(receipt)
    required = {
        "schema_version",
        "status",
        "reason_code",
        "reason_detail",
        "run_id",
        "campaign_id",
        "finding_id",
        "obligation_id",
        "experiment_id",
        "execution_id",
        "attempt_fingerprint",
        "gate_schema_version",
        "gate_receipt_id",
        "rerun_status",
        "rerun_requirements",
        "receipt_id",
        "receipt_fingerprint",
    }
    if set(row) != required:
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_receipt_fields_invalid"
        )
    if row.get("schema_version") != QUARANTINE_RECEIPT_SCHEMA:
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_receipt_schema_invalid"
        )
    if row.get("status") != "QUARANTINED" or row.get("reason_code") != (
        "UNVERIFIABLE_LEGACY_AUTHORIZATION"
    ):
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_receipt_status_invalid"
        )
    if not all(
        _text(row.get(field))
        for field in (
            "reason_detail",
            "run_id",
            "campaign_id",
            "finding_id",
            "attempt_fingerprint",
            "gate_schema_version",
            "gate_receipt_id",
        )
    ):
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_receipt_identity_missing"
        )
    expected_rerun = (
        "RERUN_REQUIRED"
        if _text(row.get("obligation_id")) and _text(row.get("experiment_id"))
        else "MANUAL_RECOMPILE_REQUIRED"
    )
    if row.get("rerun_status") != expected_rerun:
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_rerun_status_invalid"
        )
    if row.get("rerun_requirements") != list(_RERUN_REQUIREMENTS):
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_requirements_invalid"
        )
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    fingerprint = _fingerprint(unsigned)
    if (
        _text(row.get("receipt_id")) != "auth_quarantine_" + fingerprint[:24]
        or _text(row.get("receipt_fingerprint")) != fingerprint
    ):
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_receipt_fingerprint_invalid"
        )
    return dict(row)


def classify_historical_authorization_attempt(
    attempt: dict[str, Any],
    *,
    run_id: str,
    campaign_id: str,
) -> dict[str, Any]:
    """Return a quarantine receipt, or an empty dict for a currently valid attempt."""
    row = _dict(attempt)
    if (
        _text(row.get("terminal_status")).upper() != "DELIVERABLE"
        or not authorization_attempt_requires_causal_delivery(row)
    ):
        return {}
    gate = _dict(row.get("gate_receipt"))
    schema = _text(gate.get("schema_version"))
    if schema == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
        return _quarantine_receipt(
            attempt=row,
            run_id=run_id,
            campaign_id=campaign_id,
            reason_detail="LEGACY_AUTHORIZATION_GATE_V1_NO_CAUSAL_RECEIPT",
        )
    if schema != CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
        return _quarantine_receipt(
            attempt=row,
            run_id=run_id,
            campaign_id=campaign_id,
            reason_detail="AUTHORIZATION_GATE_SCHEMA_UNSUPPORTED",
        )

    bundle = _dict(row.get("delivery_evidence_bundle"))
    finding = _dict(bundle.get("finding"))
    raw_causality = _dict(finding.get("authorization_causality_receipt"))
    try:
        causality = validate_authorization_causality_receipt(raw_causality)
    except AuthorizationDeliveryGateError as exc:
        code = _error_code(exc)
        if code in {
            "authorization_causality_receipt_fields_invalid",
            "authorization_causality_receipt_schema_invalid",
        }:
            return _quarantine_receipt(
                attempt=row,
                run_id=run_id,
                campaign_id=campaign_id,
                reason_detail=code,
            )
        raise HistoricalAuthorizationQuarantineError(
            f"historical_authorization_contradiction:{code}:{exc}"
        ) from exc
    if _text(causality.get("status")).upper() != "PASSED":
        return _quarantine_receipt(
            attempt=row,
            run_id=run_id,
            campaign_id=campaign_id,
            reason_detail="authorization_delivery_causality_not_passed",
        )

    proofs = _list(finding.get("authorization_causality_binding_proofs"))
    if not proofs:
        return _quarantine_receipt(
            attempt=row,
            run_id=run_id,
            campaign_id=campaign_id,
            reason_detail="authorization_delivery_binding_proofs_missing",
        )
    try:
        for proof in proofs:
            validate_binding_materialization_identity_receipt(_dict(proof))
    except BindingMaterializationIdentityError as exc:
        raise HistoricalAuthorizationQuarantineError(
            f"historical_authorization_contradiction:{_error_code(exc)}:{exc}"
        ) from exc

    try:
        validated = validate_authorization_delivery_finding(
            finding,
            attempt=row,
            campaign_id=campaign_id,
        )
    except AuthorizationDeliveryGateError as exc:
        code = _error_code(exc)
        if code in _QUARANTINABLE_AUTHORIZATION_CODES:
            return _quarantine_receipt(
                attempt=row,
                run_id=run_id,
                campaign_id=campaign_id,
                reason_detail=code,
            )
        raise HistoricalAuthorizationQuarantineError(
            f"historical_authorization_contradiction:{code}:{exc}"
        ) from exc
    if _text(validated.get("status")).upper() != "PASSED":
        return _quarantine_receipt(
            attempt=row,
            run_id=run_id,
            campaign_id=campaign_id,
            reason_detail="authorization_delivery_causality_not_passed",
        )
    return {}


def build_historical_authorization_quarantine_projection(
    obligation_attempt_ledger: dict[str, Any],
    *,
    superseded_registry_fingerprint: str = "",
) -> dict[str, Any]:
    """Build a deterministic projection without changing the source ledger."""
    ledger = validate_obligation_attempt_ledger(_dict(obligation_attempt_ledger))
    receipts: list[dict[str, Any]] = []
    for raw in _list(ledger.get("attempts")):
        receipt = classify_historical_authorization_attempt(
            _dict(raw),
            run_id=_text(ledger.get("run_id")),
            campaign_id=_text(ledger.get("campaign_id")),
        )
        if receipt:
            receipts.append(
                validate_historical_authorization_quarantine_receipt(receipt)
            )
    receipts.sort(key=lambda value: value["finding_id"])
    finding_ids = [value["finding_id"] for value in receipts]
    rerun_queue = [
        {
            "finding_id": value["finding_id"],
            "obligation_id": value["obligation_id"],
            "experiment_id": value["experiment_id"],
            "action": value["rerun_status"],
            "requirements": list(value["rerun_requirements"]),
            "quarantine_receipt_id": value["receipt_id"],
        }
        for value in receipts
    ]
    payload = {
        "schema_version": QUARANTINE_PROJECTION_SCHEMA,
        "status": "QUARANTINED" if receipts else "CLEAR",
        "run_id": _text(ledger.get("run_id")),
        "campaign_id": _text(ledger.get("campaign_id")),
        "attempt_ledger_fingerprint": _text(ledger.get("ledger_fingerprint")),
        "superseded_registry_fingerprint": _text(
            superseded_registry_fingerprint
        ),
        "quarantine_count": len(receipts),
        "quarantined_finding_ids": finding_ids,
        "rerun_required_count": sum(
            value["rerun_status"] == "RERUN_REQUIRED" for value in receipts
        ),
        "manual_recompile_required_count": sum(
            value["rerun_status"] == "MANUAL_RECOMPILE_REQUIRED"
            for value in receipts
        ),
        "quarantine_receipts": receipts,
        "rerun_queue": rerun_queue,
    }
    return {
        **payload,
        "projection_fingerprint": _fingerprint(payload),
    }


def validate_historical_authorization_quarantine_projection(
    projection: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(projection)
    required = {
        "schema_version",
        "status",
        "run_id",
        "campaign_id",
        "attempt_ledger_fingerprint",
        "superseded_registry_fingerprint",
        "quarantine_count",
        "quarantined_finding_ids",
        "rerun_required_count",
        "manual_recompile_required_count",
        "quarantine_receipts",
        "rerun_queue",
        "projection_fingerprint",
    }
    if set(row) != required:
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_projection_fields_invalid"
        )
    if row.get("schema_version") != QUARANTINE_PROJECTION_SCHEMA:
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_projection_schema_invalid"
        )
    receipts = [
        validate_historical_authorization_quarantine_receipt(_dict(value))
        for value in _list(row.get("quarantine_receipts"))
    ]
    expected_ids = [value["finding_id"] for value in receipts]
    expected_status = "QUARANTINED" if receipts else "CLEAR"
    if (
        row.get("status") != expected_status
        or row.get("quarantine_count") != len(receipts)
        or row.get("quarantined_finding_ids") != expected_ids
    ):
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_projection_count_invalid"
        )
    unsigned = {
        key: value for key, value in row.items() if key != "projection_fingerprint"
    }
    if _text(row.get("projection_fingerprint")) != _fingerprint(unsigned):
        raise HistoricalAuthorizationQuarantineError(
            "historical_authorization_quarantine_projection_fingerprint_invalid"
        )
    return dict(row)


__all__ = [
    "HistoricalAuthorizationQuarantineError",
    "QUARANTINE_PROJECTION_SCHEMA",
    "QUARANTINE_RECEIPT_SCHEMA",
    "authorization_attempt_requires_causal_delivery",
    "build_historical_authorization_quarantine_projection",
    "classify_historical_authorization_attempt",
    "validate_historical_authorization_quarantine_projection",
    "validate_historical_authorization_quarantine_receipt",
]
