"""Compact formal authority for independently gated delivery occurrences.

A single obligation attempt may contribute multiple finding occurrences when one execution
violates several mandatory outcomes. Every entry remains tied to the same immutable attempt
fingerprint while carrying its own Gate-v2 and finding payload fingerprints.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import _formal_delivery_authority_single_occurrence_mechanics as _core
from ._formal_delivery_authority_single_occurrence_mechanics import *  # noqa: F401,F403
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
from .historical_authorization_quarantine import (
    HistoricalAuthorizationQuarantineError,
    classify_historical_authorization_attempt,
)
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    delivery_occurrence_views,
    validate_obligation_attempt_ledger,
)

FORMAL_DELIVERY_AUTHORITY_SCHEMA = _core.FORMAL_DELIVERY_AUTHORITY_SCHEMA
_ENTRY_FIELDS = set(_core._ENTRY_FIELDS)
_RECEIPT_FIELDS = set(_core._RECEIPT_FIELDS)
FormalDeliveryAuthorityError = _core.FormalDeliveryAuthorityError


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


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
    occurrence_keys: list[tuple[str, str]] = []
    for raw in attempts:
        entry = _dict(raw)
        if set(entry) != _ENTRY_FIELDS or not all(
            _text(entry.get(field)) for field in _ENTRY_FIELDS
        ):
            raise FormalDeliveryAuthorityError(
                "formal_authority_attempt_entry_invalid"
            )
        attempt_ids.append(_text(entry.get("finding_id")))
        occurrence_keys.append(
            (_text(entry.get("obligation_id")), _text(entry.get("finding_id")))
        )
    if attempts != sorted(attempts, key=lambda item: _text(item.get("finding_id"))):
        raise FormalDeliveryAuthorityError(
            "formal_authority_attempts_not_canonical"
        )
    if len(attempt_ids) != len(set(attempt_ids)):
        raise FormalDeliveryAuthorityError(
            "formal_authority_finding_id_duplicate"
        )
    if len(occurrence_keys) != len(set(occurrence_keys)):
        raise FormalDeliveryAuthorityError(
            "formal_authority_occurrence_identity_duplicate"
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
    expected = _fingerprint(
        {key: value for key, value in row.items() if key != "receipt_fingerprint"}
    )
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
    obligation_attempt_ledger_prevalidated: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        mainline = validate_mainline_run_contract(mainline_run)
        # The caller may pass a ledger it already validated (canonical
        # defect registry / redaction chain validates once and reuses):
        # re-validating re-serializes the ENTIRE ledger (json.dumps of every
        # attempt's execution evidence) — with the execution-budget fix the
        # ledger grew large and repeated validation stalled the delivery
        # phase for tens of minutes. Semantics unchanged: the ledger is
        # still fully validated, exactly once per object.
        ledger = (
            obligation_attempt_ledger_prevalidated
            if obligation_attempt_ledger_prevalidated is not None
            else validate_obligation_attempt_ledger(obligation_attempt_ledger)
        )
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
    for raw_parent in _list(ledger.get("attempts")):
        parent = _dict(raw_parent)
        if _text(parent.get("terminal_status")).upper() != "DELIVERABLE":
            continue
        for attempt in delivery_occurrence_views(parent):
            finding_id = _text(attempt.get("finding_id"))
            try:
                quarantine = classify_historical_authorization_attempt(
                    attempt,
                    run_id=mainline["run_id"],
                    campaign_id=mainline["campaign_id"],
                )
            except HistoricalAuthorizationQuarantineError as exc:
                raise FormalDeliveryAuthorityError(
                    f"formal_authority_historical_authorization_invalid:{finding_id}:{exc}"
                ) from exc
            if quarantine:
                continue
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
            entries.append(
                {
                    "obligation_id": _text(attempt.get("obligation_id")),
                    "experiment_id": _text(attempt.get("experiment_id")),
                    "execution_id": _text(attempt.get("execution_id")),
                    "finding_id": finding_id,
                    "attempt_fingerprint": _text(parent.get("attempt_fingerprint")),
                    "gate_receipt_id": _text(gate.get("gate_receipt_id")),
                    "gate_output_fingerprint": _text(gate.get("output_fingerprint")),
                    "finding_payload_fingerprint": _text(
                        gate.get("finding_payload_fingerprint")
                        or finding_payload_fingerprint(finding)
                    ),
                }
            )
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


_core.validate_formal_delivery_authority_receipt = (
    validate_formal_delivery_authority_receipt
)

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
