from __future__ import annotations

"""Low-level occurrence authority shared by delivery consumers.

This module deliberately sits below quality projection and canonical identity.
It performs only the exact Gate-v2 + attempt-ledger join, preventing circular
dependencies and preventing higher layers from inventing a second formal
finding scope. Authorization occurrences additionally consume the causal receipt
already sealed into the Gate-v2 finding payload; no frontend or projection layer
may infer authorization deliverability from mutable display flags.
"""

from typing import Any

from .authorization_delivery_gate import (
    AuthorizationDeliveryGateError,
    validate_authorization_delivery_finding,
)
from .binding_materialization_identity_receipt import (
    BindingMaterializationIdentityError,
    validate_binding_materialization_identity_receipt,
)
from .customer_delivery_gate import LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
from .customer_delivery_gate_v2 import (
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    DeliveryGateV2Error,
    validate_customer_delivery_gate_receipt_v2,
)
from .discovery_mainline_contract import MainlineContractError
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    validate_obligation_attempt_ledger,
)

_AUTHORIZATION_FAMILIES = frozenset({"authorization", "isolation", "visibility"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _authorization_attempt(attempt: dict[str, Any]) -> bool:
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


def finding_id(item: dict[str, Any]) -> str:
    return _text(item.get("finding_id") or item.get("id") or item.get("bug_id"))


def validated_deliverable_gate_index(
    ledger: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if ledger is None:
        return {}
    try:
        validated = validate_obligation_attempt_ledger(_dict(ledger))
    except ObligationAttemptLedgerError as exc:
        raise MainlineContractError(
            f"formal_attempt_ledger_invalid:{exc}"
        ) from exc
    campaign_id = _text(validated.get("campaign_id"))
    index: dict[str, dict[str, Any]] = {}
    for raw in _list(validated.get("attempts")):
        attempt = _dict(raw)
        if _text(attempt.get("terminal_status")).upper() != "DELIVERABLE":
            continue
        occurrence_id = _text(attempt.get("finding_id"))
        gate = _dict(attempt.get("gate_receipt"))
        schema_version = gate.get("schema_version")
        if not occurrence_id:
            raise MainlineContractError("formal_deliverable_gate_v2_missing")
        if schema_version == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
            evidence_bundle = _dict(attempt.get("delivery_evidence_bundle"))
            bundled_finding = _dict(evidence_bundle.get("finding"))
            try:
                validated_gate = validate_customer_delivery_gate_receipt_v2(
                    gate,
                    finding=bundled_finding or None,
                )
            except DeliveryGateV2Error as exc:
                raise MainlineContractError(
                    f"formal_deliverable_gate_invalid:{occurrence_id}:{exc}"
                ) from exc
            if (
                _text(_dict(validated_gate.get("identity")).get("finding_id"))
                != occurrence_id
            ):
                raise MainlineContractError(
                    f"formal_deliverable_gate_identity_mismatch:{occurrence_id}"
                )
            try:
                for proof in _list(
                    bundled_finding.get(
                        "authorization_causality_binding_proofs"
                    )
                ):
                    validate_binding_materialization_identity_receipt(
                        _dict(proof)
                    )
                validate_authorization_delivery_finding(
                    bundled_finding,
                    attempt=attempt,
                    campaign_id=campaign_id,
                )
            except (
                AuthorizationDeliveryGateError,
                BindingMaterializationIdentityError,
            ) as exc:
                raise MainlineContractError(
                    f"formal_authorization_delivery_invalid:{occurrence_id}:{exc}"
                ) from exc
        elif (
            schema_version == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
            and _text(gate.get("status")).upper() == "DELIVERABLE"
            and _text(gate.get("finding_id")) == occurrence_id
        ):
            if _authorization_attempt(attempt):
                raise MainlineContractError(
                    f"formal_authorization_delivery_v2_required:{occurrence_id}"
                )
            validated_gate = dict(gate)
        else:
            raise MainlineContractError("formal_deliverable_gate_v2_missing")
        if occurrence_id in index:
            raise MainlineContractError(
                f"formal_finding_id_duplicate:{occurrence_id}"
            )
        index[occurrence_id] = validated_gate
    return index


def formal_customer_deliverable_findings(
    findings: Any,
    *,
    obligation_attempt_ledger: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Join findings to the exact validated DELIVERABLE occurrence set."""

    gate_by_finding_id = validated_deliverable_gate_index(
        obligation_attempt_ledger
    )
    if not gate_by_finding_id:
        return []
    deliverable: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _list(findings):
        if not isinstance(item, dict):
            continue
        occurrence_id = finding_id(item)
        expected_gate = gate_by_finding_id.get(occurrence_id)
        if expected_gate is None:
            continue
        embedded_gate = _dict(item.get("delivery_gate_receipt"))
        if not embedded_gate:
            embedded_gate = dict(expected_gate)
        if embedded_gate != expected_gate:
            raise MainlineContractError(
                f"formal_finding_gate_receipt_mismatch:{occurrence_id}"
            )
        schema_version = embedded_gate.get("schema_version")
        if schema_version == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
            try:
                validate_customer_delivery_gate_receipt_v2(
                    embedded_gate,
                    finding=item,
                )
            except DeliveryGateV2Error as exc:
                raise MainlineContractError(
                    f"formal_finding_gate_invalid:{occurrence_id}:{exc}"
                ) from exc
        elif schema_version != LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
            raise MainlineContractError(
                f"formal_finding_gate_invalid:{occurrence_id}:schema_unsupported"
            )
        deliverable.append(dict(item))
        seen.add(occurrence_id)
    missing = sorted(set(gate_by_finding_id) - seen)
    if missing:
        raise MainlineContractError(
            f"formal_deliverable_finding_missing:{missing[0]}"
        )
    return deliverable


def validated_delivery_gate_finding_ids(
    ledger: dict[str, Any] | None,
) -> list[str]:
    """Return the exact occurrence IDs proven by Gate-v2 attempts."""

    return sorted(validated_deliverable_gate_index(ledger))