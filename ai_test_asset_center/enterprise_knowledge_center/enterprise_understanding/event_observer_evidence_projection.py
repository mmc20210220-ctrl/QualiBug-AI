"""Normalize formal-event evidence into the enterprise-understanding evidence contract."""
from __future__ import annotations

from typing import Any, Iterable

from .schema import as_dict, as_list, dedupe_evidence, source_evidence, text

_EVENT_KIND = "SOURCE_EVENT_DELIVERY_OBSERVER"


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _normalized_evidence(observer: dict[str, Any]) -> list[dict[str, Any]]:
    contract_ref = text(observer.get("event_contract_ref"))
    contract = as_dict(observer.get("event_contract"))
    raw_rows = _dicts(observer.get("evidence"))
    if not raw_rows:
        raw_rows = _dicts(contract.get("source_refs"))
    if not raw_rows and text(contract.get("source_id")):
        raw_rows = [contract]
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = source_evidence(
            source_id=raw.get("source_id"),
            source_locator=raw.get("source_locator") or raw.get("locator"),
            quote=raw.get("quote") or raw.get("source_quote"),
            quote_hash=raw.get("quote_hash"),
            asset_ref=contract_ref,
            derivation="exact_formal_event_contract_identity",
        )
        if row:
            rows.append(row)
    return dedupe_evidence(rows)


def project_event_observer_evidence(
    bindings: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return bindings whose event observers and parent binding share source evidence."""
    result: list[dict[str, Any]] = []
    for raw_binding in bindings:
        if not isinstance(raw_binding, dict):
            continue
        binding = dict(raw_binding)
        event_evidence: list[dict[str, Any]] = []
        for key in ("condition_observer_bindings", "effect_observer_bindings"):
            slots: list[dict[str, Any]] = []
            for raw_slot in _dicts(binding.get(key)):
                slot = dict(raw_slot)
                candidates: list[dict[str, Any]] = []
                for raw_candidate in _dicts(slot.get("bindings")):
                    candidate = dict(raw_candidate)
                    if text(candidate.get("binding_kind")) == _EVENT_KIND:
                        evidence = _normalized_evidence(candidate)
                        candidate["evidence"] = evidence
                        candidate["evidence_contract"] = (
                            "ENTERPRISE_SOURCE_ID_AND_LOCATOR"
                        )
                        event_evidence.extend(evidence)
                    candidates.append(candidate)
                slot["bindings"] = candidates
                slots.append(slot)
            binding[key] = slots
        direct: list[dict[str, Any]] = []
        for raw_observer in _dicts(binding.get("formal_event_observer_bindings")):
            observer = dict(raw_observer)
            evidence = _normalized_evidence(observer)
            observer["evidence"] = evidence
            observer["evidence_contract"] = "ENTERPRISE_SOURCE_ID_AND_LOCATOR"
            event_evidence.extend(evidence)
            direct.append(observer)
        binding["formal_event_observer_bindings"] = direct
        binding["evidence"] = dedupe_evidence(
            [
                *[
                    row
                    for row in as_list(binding.get("evidence"))
                    if isinstance(row, dict)
                ],
                *event_evidence,
            ]
        )
        binding["formal_event_observer_evidence_count"] = len(
            dedupe_evidence(event_evidence)
        )
        result.append(binding)
    return result


__all__ = ["project_event_observer_evidence"]
