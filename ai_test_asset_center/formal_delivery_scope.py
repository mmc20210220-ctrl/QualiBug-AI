from __future__ import annotations

"""Occurrence authority with multi-outcome attempt support.

One obligation attempt may carry several independently gated findings. This module joins
customer findings to every validated occurrence view without weakening Gate-v2, historical
quarantine, or immutable attempt-ledger authority.
"""

from typing import Any

from . import _formal_delivery_scope_single_occurrence_mechanics as _core
from ._formal_delivery_scope_single_occurrence_mechanics import *  # noqa: F401,F403
from ._delivery_validation_cache import (
    GATE_INDEX_CACHE,
    _MISSING,
    content_fingerprint,
)
from .customer_delivery_gate import LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
from .customer_delivery_gate_v2 import (
    CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
    DeliveryGateV2Error,
    validate_customer_delivery_gate_receipt_v2,
)
from .discovery_mainline_contract import MainlineContractError
from .historical_authorization_quarantine import (
    HistoricalAuthorizationQuarantineError,
    classify_historical_authorization_attempt,
)
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    delivery_occurrence_views,
    validate_obligation_attempt_ledger,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def finding_id(item: dict[str, Any]) -> str:
    return _text(item.get("finding_id") or item.get("id") or item.get("bug_id"))


def validated_deliverable_gate_index(
    ledger: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if ledger is None:
        return {}
    # The gate index is a pure function of the sealed obligation-attempt
    # ledger content: the same content always yields the same index, and the
    # whole formal findings path re-derives it ~7 times per run.  The key is
    # the ledger's own content address, recomputed from the CURRENT input, so
    # any content change (including an in-place mutation of the ledger dict)
    # produces a different key and forces full re-validation — the mutated
    # ledger then fails its fingerprint check exactly as it would without the
    # cache.  Failures are never cached, so no fail-closed gate is relaxed.
    cache_key = content_fingerprint(ledger)
    cached = GATE_INDEX_CACHE.get(cache_key)
    if cached is not _MISSING:
        return dict(cached)
    try:
        validated = validate_obligation_attempt_ledger(_dict(ledger))
    except ObligationAttemptLedgerError as exc:
        raise MainlineContractError(f"formal_attempt_ledger_invalid:{exc}") from exc
    run_id = _text(validated.get("run_id"))
    campaign_id = _text(validated.get("campaign_id"))
    index: dict[str, dict[str, Any]] = {}
    for raw in _list(validated.get("attempts")):
        parent = _dict(raw)
        if _text(parent.get("terminal_status")).upper() != "DELIVERABLE":
            continue
        for attempt in delivery_occurrence_views(parent):
            occurrence_id = _text(attempt.get("finding_id"))
            gate = _dict(attempt.get("gate_receipt"))
            schema_version = gate.get("schema_version")
            if not occurrence_id:
                raise MainlineContractError("formal_deliverable_gate_v2_missing")
            try:
                quarantine = classify_historical_authorization_attempt(
                    attempt,
                    run_id=run_id,
                    campaign_id=campaign_id,
                )
            except HistoricalAuthorizationQuarantineError as exc:
                raise MainlineContractError(
                    f"historical_authorization_quarantine_invalid:{occurrence_id}:{exc}"
                ) from exc
            if quarantine:
                continue
            if schema_version == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA:
                bundle = _dict(attempt.get("delivery_evidence_bundle"))
                bundled_finding = _dict(bundle.get("finding"))
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
            elif (
                schema_version == LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
                and _text(gate.get("status")).upper() == "DELIVERABLE"
                and _text(gate.get("finding_id")) == occurrence_id
            ):
                validated_gate = dict(gate)
            else:
                raise MainlineContractError("formal_deliverable_gate_v2_missing")
            if occurrence_id in index:
                raise MainlineContractError(
                    f"formal_finding_id_duplicate:{occurrence_id}"
                )
            index[occurrence_id] = validated_gate
    GATE_INDEX_CACHE.put(cache_key, index)
    return index


def formal_customer_deliverable_findings(
    findings: Any,
    *,
    obligation_attempt_ledger: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    gate_by_finding_id = validated_deliverable_gate_index(obligation_attempt_ledger)
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
        embedded_gate = _dict(item.get("delivery_gate_receipt")) or dict(expected_gate)
        if embedded_gate != expected_gate:
            # Content-addressed fields (gate_receipt_id, input/output
            # fingerprints, and any nested receipt fingerprint) are recomputed
            # by reseal_obligation_attempt_ledger after artifact redaction
            # rewrote secret-bearing strings inside the sealed receipts:
            # redaction changes content, so a content-addressed id MUST change.
            # The occurrence's embedded copy is not resealed, so those fields
            # legitimately differ on a redacted envelope. Semantic identity
            # fields (status, identity, reason codes, adjudication, receipt
            # ids) must still match exactly.
            def _semantic_equal(left: Any, right: Any, key: str = "") -> bool:
                if key.endswith("fingerprint") or key in {
                    "gate_receipt_id",
                    "input_fingerprint",
                    "output_fingerprint",
                }:
                    return True
                if isinstance(left, dict) and isinstance(right, dict):
                    return all(
                        _semantic_equal(
                            left.get(k), right.get(k), str(k)
                        )
                        for k in set(left) | set(right)
                    )
                if isinstance(left, list) and isinstance(right, list):
                    return len(left) == len(right) and all(
                        _semantic_equal(a, b, key)
                        for a, b in zip(left, right)
                    )
                return left == right

            if not _semantic_equal(embedded_gate, expected_gate):
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
    return sorted(validated_deliverable_gate_index(ledger))


_core.validated_deliverable_gate_index = validated_deliverable_gate_index
_core.formal_customer_deliverable_findings = formal_customer_deliverable_findings

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
