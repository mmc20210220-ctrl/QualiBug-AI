"""Compact, content-addressed authority for externally scored findings.

The receipt is built only after the complete Gate-v2 evidence bundle and the
obligation-attempt ledger have been revalidated.  It contains no request or
response bodies, so it can cross persistence and evaluator boundaries without
rewriting the signed evidence chain.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .customer_delivery_gate import LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
from .customer_delivery_gate_v2 import (
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    DeliveryGateV2Error,
    finding_payload_fingerprint,
    validate_customer_delivery_gate_receipt_v2,
)
from .discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)
from .formal_delivery_scope import formal_customer_deliverable_findings
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    validate_obligation_attempt_ledger,
)


FORMAL_DELIVERY_AUTHORITY_SCHEMA = "qualibug.formal-delivery-authority.v2"

_ENTRY_FIELDS = {
    "obligation_id",
    "experiment_id",
    "execution_id",
    "finding_id",
    "attempt_fingerprint",
    "gate_receipt_id",
    "gate_output_fingerprint",
    "finding_payload_fingerprint",
}
_RECEIPT_FIELDS = {
    "schema_version",
    "status",
    "run_id",
    "campaign_id",
    "target_id",
    "environment_id",
    "policy_version",
    "evaluation_mode",
    "mainline_contract_fingerprint",
    "attempt_ledger_fingerprint",
    "gate_schema_version",
    "delivery_occurrence_count",
    "delivery_occurrence_finding_ids",
    "deliverable_attempts",
    "receipt_fingerprint",
}


class FormalDeliveryAuthorityError(ValueError):
    """The formal authority is missing, foreign, or internally inconsistent."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_formal_delivery_authority_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(receipt)
    if set(row) != _RECEIPT_FIELDS:
        raise FormalDeliveryAuthorityError("formal_authority_fields_invalid")
    if row.get("schema_version") != FORMAL_DELIVERY_AUTHORITY_SCHEMA:
        raise FormalDeliveryAuthorityError("formal_authority_schema_invalid")
    if row.get("status") != "VERIFIED":
        raise FormalDeliveryAuthorityError("formal_authority_status_invalid")
    for field in (
        "run_id",
        "campaign_id",
        "target_id",
        "environment_id",
        "policy_version",
        "evaluation_mode",
        "mainline_contract_fingerprint",
        "attempt_ledger_fingerprint",
    ):
        if not _text(row.get(field)):
            raise FormalDeliveryAuthorityError(
                f"formal_authority_identity_missing:{field}"
            )
    if row.get("gate_schema_version") != CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
        raise FormalDeliveryAuthorityError("formal_authority_gate_schema_invalid")
    formal_ids = row.get("delivery_occurrence_finding_ids")
    if not isinstance(formal_ids, list):
        raise FormalDeliveryAuthorityError("formal_authority_ids_invalid")
    normalized_ids = sorted({_text(value) for value in formal_ids if _text(value)})
    if normalized_ids != formal_ids:
        raise FormalDeliveryAuthorityError("formal_authority_ids_not_canonical")
    attempts = row.get("deliverable_attempts")
    if not isinstance(attempts, list):
        raise FormalDeliveryAuthorityError("formal_authority_attempts_invalid")
    attempt_ids: list[str] = []
    obligation_ids: list[str] = []
    for raw in attempts:
        entry = _dict(raw)
        if set(entry) != _ENTRY_FIELDS or not all(
            _text(entry.get(field)) for field in _ENTRY_FIELDS
        ):
            raise FormalDeliveryAuthorityError(
                "formal_authority_attempt_entry_invalid"
            )
        attempt_ids.append(_text(entry.get("finding_id")))
        obligation_ids.append(_text(entry.get("obligation_id")))
    if attempts != sorted(attempts, key=lambda item: _text(item.get("finding_id"))):
        raise FormalDeliveryAuthorityError(
            "formal_authority_attempts_not_canonical"
        )
    if len(attempt_ids) != len(set(attempt_ids)):
        raise FormalDeliveryAuthorityError(
            "formal_authority_finding_id_duplicate"
        )
    if len(obligation_ids) != len(set(obligation_ids)):
        raise FormalDeliveryAuthorityError(
            "formal_authority_obligation_id_duplicate"
        )
    if attempt_ids != formal_ids:
        raise FormalDeliveryAuthorityError(
            "formal_authority_attempt_id_mismatch"
        )
    if int(row.get("delivery_occurrence_count") or 0) != len(formal_ids):
        raise FormalDeliveryAuthorityError(
            "formal_authority_count_mismatch"
        )
    observed = _text(row.get("receipt_fingerprint"))
    expected = _fingerprint({
        key: value for key, value in row.items() if key != "receipt_fingerprint"
    })
    if not observed or observed != expected:
        raise FormalDeliveryAuthorityError(
            "formal_authority_fingerprint_invalid"
        )
    return dict(row)


def build_formal_delivery_authority_receipt(
    *,
    mainline_run: dict[str, Any],
    findings: list[dict[str, Any]],
    obligation_attempt_ledger: dict[str, Any],
) -> dict[str, Any]:
    """Revalidate the full authority chain and emit a compact audit receipt."""

    try:
        mainline = validate_mainline_run_contract(mainline_run)
        ledger = validate_obligation_attempt_ledger(obligation_attempt_ledger)
        formal_findings = formal_customer_deliverable_findings(
            findings,
            obligation_attempt_ledger=ledger,
        )
    except (
        MainlineContractError,
        ObligationAttemptLedgerError,
        DeliveryGateV2Error,
        TypeError,
        ValueError,
    ) as exc:
        raise FormalDeliveryAuthorityError(
            f"formal_authority_dependency_invalid:{type(exc).__name__}:{exc}"
        ) from exc

    expected_ledger_identity = {
        "run_id": mainline["run_id"],
        "campaign_id": mainline["campaign_id"],
        "mainline_contract_fingerprint": mainline["contract_fingerprint"],
    }
    for field, value in expected_ledger_identity.items():
        if _text(ledger.get(field)) != value:
            raise FormalDeliveryAuthorityError(
                f"formal_authority_ledger_identity_mismatch:{field}"
            )

    finding_by_id = {
        _text(item.get("finding_id") or item.get("id")): item
        for item in formal_findings
    }
    if len(finding_by_id) != len(formal_findings):
        raise FormalDeliveryAuthorityError(
            "formal_authority_finding_identity_invalid"
        )
    entries: list[dict[str, str]] = []
    for raw_attempt in _list(ledger.get("attempts")):
        attempt = _dict(raw_attempt)
        if _text(attempt.get("terminal_status")).upper() != "DELIVERABLE":
            continue
        finding_id = _text(attempt.get("finding_id"))
        finding = finding_by_id.get(finding_id)
        if finding is None:
            raise FormalDeliveryAuthorityError(
                f"formal_authority_finding_missing:{finding_id}"
            )
        gate_receipt = _dict(attempt.get("gate_receipt"))
        if (
            gate_receipt.get("schema_version")
            == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
        ):
            if (
                _text(gate_receipt.get("status")).upper() != "DELIVERABLE"
                or _text(gate_receipt.get("finding_id")) != finding_id
            ):
                raise FormalDeliveryAuthorityError(
                    f"formal_authority_gate_invalid:{finding_id}:legacy_gate_mismatch"
                )
            gate = gate_receipt
        else:
            try:
                gate = validate_customer_delivery_gate_receipt_v2(
                    gate_receipt,
                    finding=finding,
                )
            except DeliveryGateV2Error as exc:
                raise FormalDeliveryAuthorityError(
                    f"formal_authority_gate_invalid:{finding_id}:{exc}"
                ) from exc
            identity = _dict(gate.get("identity"))
            expected_gate_identity = {
                "run_id": mainline["run_id"],
                "campaign_id": mainline["campaign_id"],
                "target_id": mainline["target_id"],
                "environment_id": mainline["environment_id"],
                "mainline_contract_fingerprint": mainline[
                    "contract_fingerprint"
                ],
                "finding_id": finding_id,
            }
            for field, value in expected_gate_identity.items():
                if _text(identity.get(field)) != value:
                    raise FormalDeliveryAuthorityError(
                        f"formal_authority_gate_identity_mismatch:{field}"
                    )
        entries.append({
            "obligation_id": _text(attempt.get("obligation_id")),
            "experiment_id": _text(attempt.get("experiment_id")),
            "execution_id": _text(attempt.get("execution_id")),
            "finding_id": finding_id,
            "attempt_fingerprint": _text(attempt.get("attempt_fingerprint")),
            "gate_receipt_id": _text(gate.get("gate_receipt_id")),
            "gate_output_fingerprint": _text(gate.get("output_fingerprint")),
            "finding_payload_fingerprint": _text(
                gate.get("finding_payload_fingerprint")
                or finding_payload_fingerprint(finding)
            ),
        })
    entries.sort(key=lambda item: item["finding_id"])
    formal_ids = sorted(finding_by_id)
    if [entry["finding_id"] for entry in entries] != formal_ids:
        raise FormalDeliveryAuthorityError(
            "formal_authority_deliverable_attempt_mismatch"
        )

    payload: dict[str, Any] = {
        "schema_version": FORMAL_DELIVERY_AUTHORITY_SCHEMA,
        "status": "VERIFIED",
        "run_id": mainline["run_id"],
        "campaign_id": mainline["campaign_id"],
        "target_id": mainline["target_id"],
        "environment_id": mainline["environment_id"],
        "policy_version": mainline["policy_version"],
        "evaluation_mode": mainline["evaluation_mode"],
        "mainline_contract_fingerprint": mainline["contract_fingerprint"],
        "attempt_ledger_fingerprint": _text(ledger.get("ledger_fingerprint")),
        "gate_schema_version": CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
        "delivery_occurrence_count": len(formal_ids),
        "delivery_occurrence_finding_ids": formal_ids,
        "deliverable_attempts": entries,
    }
    payload["receipt_fingerprint"] = _fingerprint(payload)
    return validate_formal_delivery_authority_receipt(payload)
