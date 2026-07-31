"""Source-version lifecycle for governed implicit rules.

The active ``rule_library`` remains the only execution authority.  This module keeps
historical implicit rules in an append-only lifecycle ledger when their source is
removed, replaced, or no longer re-derives the rule.  A stale rule is therefore
visible and auditable without being emitted into Behavior IR.

No second source registry is created.  Source identities are projected from the
existing source inventory/registry fields already carried by the enterprise asset.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

SCHEMA_VERSION = "qualibug.implicit-rule-lifecycle.v1"
_DERIVATION = "implicit_rule_entailment"
_INVALID_SOURCE_IDS = frozenset({"", "unknown", "unspecified", "*"})
_TERMINAL_NON_EXECUTABLE = frozenset({"STALE", "REJECTED", "SUPERSEDED"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(_canonical(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _source_id(row: dict[str, Any]) -> str:
    return _text(row.get("source_id") or row.get("asset_id") or row.get("id"))


def _source_hash(row: dict[str, Any]) -> str:
    manifest = _dict(row.get("manifest"))
    return _text(
        row.get("latest_source_hash")
        or row.get("source_hash")
        or row.get("content_hash")
        or row.get("text_hash")
        or row.get("hash")
        or manifest.get("source_hash")
    ).removeprefix("sha256:")


def _source_version_id(row: dict[str, Any]) -> str:
    manifest = _dict(row.get("manifest"))
    return _text(
        row.get("latest_version_id")
        or row.get("source_version_id")
        or row.get("version_id")
        or manifest.get("source_version_id")
        or manifest.get("version_id")
    )


def _source_active(row: dict[str, Any]) -> bool:
    status = _text(row.get("status") or row.get("lifecycle_status")).upper()
    if row.get("active") is False or row.get("deactivated") is True:
        return False
    return status not in {"INACTIVE", "DEACTIVATED", "REMOVED", "DELETED"}


def _iter_source_rows(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                yield row
        return
    if not isinstance(value, dict):
        return
    assets = value.get("assets")
    if isinstance(assets, dict):
        for asset_id, raw in assets.items():
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("source_id", asset_id)
            yield row
        return
    if isinstance(assets, list):
        for row in assets:
            if isinstance(row, dict):
                yield row
        return
    for asset_id, raw in value.items():
        if not isinstance(raw, dict):
            continue
        if not any(
            key in raw
            for key in (
                "source_id",
                "latest_source_hash",
                "source_hash",
                "source_version_id",
                "latest_version_id",
                "versions",
            )
        ):
            continue
        row = dict(raw)
        row.setdefault("source_id", asset_id)
        yield row


def active_source_version_inventory(
    asset: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Project active source versions from existing inventory authorities.

    ``authoritative`` is false when the asset contains no inventory-shaped field.  In
    that case a missing source cannot be interpreted as deactivation; lifecycle falls
    back to the conservative ``RULE_NOT_REDERIVED`` reason.
    """

    inventory: dict[str, dict[str, Any]] = {}
    authoritative = False
    for field in (
        "enterprise_source_registry",
        "source_registry",
        "source_inventory",
        "sources",
    ):
        if field not in asset:
            continue
        raw = asset.get(field)
        if isinstance(raw, (list, dict)):
            authoritative = True
        for row in _iter_source_rows(raw):
            source_id = _source_id(row)
            if not source_id or source_id.lower() in _INVALID_SOURCE_IDS:
                continue
            if not _source_active(row):
                continue
            inventory[source_id] = {
                "source_id": source_id,
                "source_hash": _source_hash(row),
                "source_version_id": _source_version_id(row),
                "source_type": _text(row.get("source_type") or row.get("type")),
            }
    return inventory, authoritative


def _version_ref(
    source_id: str,
    *,
    source_hash: str = "",
    source_version_id: str = "",
) -> dict[str, str]:
    return {
        "source_id": source_id,
        "source_hash": _text(source_hash),
        "source_version_id": _text(source_version_id),
    }


def _dedupe_version_refs(rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    by_source: dict[str, dict[str, str]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        source_id = _source_id(raw)
        if not source_id or source_id.lower() in _INVALID_SOURCE_IDS:
            continue
        current = by_source.get(source_id, _version_ref(source_id))
        source_hash = _source_hash(raw) or current["source_hash"]
        version_id = _source_version_id(raw) or current["source_version_id"]
        by_source[source_id] = _version_ref(
            source_id,
            source_hash=source_hash,
            source_version_id=version_id,
        )
    return [by_source[key] for key in sorted(by_source)]


def annotate_rule_candidates_with_source_versions(
    candidates: list[dict[str, Any]],
    asset: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attach current immutable source identities before validation/promotion."""

    inventory, authoritative = active_source_version_inventory(asset)
    result: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        refs: list[dict[str, Any]] = []
        for source_ref in _list(candidate.get("source_refs")):
            if not isinstance(source_ref, dict):
                continue
            row = dict(source_ref)
            source_id = _source_id(row)
            current = inventory.get(source_id, {})
            if current:
                row.setdefault("source_hash", current.get("source_hash"))
                row.setdefault("source_version_id", current.get("source_version_id"))
            refs.append(row)
        candidate["source_refs"] = refs
        version_refs = _dedupe_version_refs(
            [*refs, *[_version_ref(value) for value in _list(candidate.get("supporting_source_ids")) if _text(value)]]
        )
        candidate["source_version_refs"] = version_refs
        candidate["source_snapshot_fingerprint"] = _stable_id(
            "source_snapshot", version_refs
        )
        candidate["source_version_inventory_authoritative"] = authoritative
        result.append(candidate)
    return result


def _rule_version_refs(rule: dict[str, Any]) -> list[dict[str, str]]:
    explicit = _dedupe_version_refs(_list(rule.get("source_version_refs")))
    if explicit:
        return explicit
    refs = _dedupe_version_refs(_list(rule.get("source_refs")))
    if refs:
        return refs
    return _dedupe_version_refs(
        [_version_ref(value) for value in _list(rule.get("source_ids")) if _text(value)]
    )


def _source_snapshot_fingerprint(rule: dict[str, Any]) -> str:
    return _text(rule.get("source_snapshot_fingerprint")) or _stable_id(
        "source_snapshot", _rule_version_refs(rule)
    )


def _event(
    *,
    rule_id: str,
    from_status: str,
    to_status: str,
    reason: str,
    previous_source_versions: list[dict[str, Any]],
    current_source_versions: list[dict[str, Any]],
    authority_decision_ref: str = "",
) -> dict[str, Any]:
    event_id = _stable_id(
        "implicit_rule_lifecycle_event",
        rule_id,
        from_status,
        to_status,
        reason,
        previous_source_versions,
        current_source_versions,
        authority_decision_ref,
    )
    return {
        "event_id": event_id,
        "rule_id": rule_id,
        "from_status": from_status,
        "to_status": to_status,
        "reason": reason,
        "previous_source_versions": previous_source_versions,
        "current_source_versions": current_source_versions,
        "authority_decision_ref": authority_decision_ref,
    }


def _prior_items(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ledger = _dict(asset.get("implicit_rule_lifecycle_ledger"))
    return {
        _text(row.get("rule_id")): dict(row)
        for row in _list(ledger.get("items"))
        if isinstance(row, dict) and _text(row.get("rule_id"))
    }


def _prior_events(asset: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = _dict(asset.get("implicit_rule_lifecycle_ledger"))
    return [
        dict(row)
        for row in _list(ledger.get("events"))
        if isinstance(row, dict) and _text(row.get("event_id"))
    ]


def _current_refs_for_prior_rule(
    rule: dict[str, Any], inventory: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    return _dedupe_version_refs(
        [inventory[source_id] for source_id in _list(rule.get("source_ids")) if source_id in inventory]
    )


def _stale_reason(
    rule: dict[str, Any],
    *,
    inventory: dict[str, dict[str, Any]],
    inventory_authoritative: bool,
) -> tuple[str, list[dict[str, str]]]:
    previous = _rule_version_refs(rule)
    source_ids = [
        source_id
        for source_id in (_text(row.get("source_id")) for row in previous)
        if source_id
    ]
    current = _current_refs_for_prior_rule(rule, inventory)
    if inventory_authoritative and source_ids and any(
        source_id not in inventory for source_id in source_ids
    ):
        return "SOURCE_DEACTIVATED", current
    current_by_id = {row["source_id"]: row for row in current}
    changed = False
    for prior in previous:
        source_id = prior["source_id"]
        now = current_by_id.get(source_id)
        if not now:
            continue
        prior_hash = _text(prior.get("source_hash"))
        prior_version = _text(prior.get("source_version_id"))
        if prior_hash and _text(now.get("source_hash")) and prior_hash != _text(now.get("source_hash")):
            changed = True
        if prior_version and _text(now.get("source_version_id")) and prior_version != _text(now.get("source_version_id")):
            changed = True
    if changed:
        return "SOURCE_VERSION_CHANGED_RULE_NOT_REDERIVED", current
    return "RULE_NOT_REDERIVED_FROM_CURRENT_AUTHORITY", current


def project_implicit_rule_lifecycle(
    asset: dict[str, Any],
    *,
    prior_derived_rules: list[dict[str, Any]],
    accepted_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the deterministic active/stale lifecycle ledger for this projection."""

    inventory, inventory_authoritative = active_source_version_inventory(asset)
    previous_items = _prior_items(asset)
    previous_events = _prior_events(asset)

    for rule in prior_derived_rules:
        if not isinstance(rule, dict) or _text(rule.get("derivation")) != _DERIVATION:
            continue
        rule_id = _text(rule.get("rule_id"))
        if not rule_id:
            continue
        previous_items[rule_id] = {
            **previous_items.get(rule_id, {}),
            "rule_id": rule_id,
            "status": "ACTIVE",
            "execution_allowed": True,
            "rule_snapshot": dict(rule),
            "source_version_refs": _rule_version_refs(rule),
            "source_snapshot_fingerprint": _source_snapshot_fingerprint(rule),
        }

    accepted_by_id = {
        _text(rule.get("rule_id")): dict(rule)
        for rule in accepted_rules
        if isinstance(rule, dict) and _text(rule.get("rule_id"))
    }
    items: dict[str, dict[str, Any]] = {}
    events = list(previous_events)
    event_ids = {_text(row.get("event_id")) for row in events}

    def append_event(row: dict[str, Any]) -> None:
        event_id = _text(row.get("event_id"))
        if event_id and event_id not in event_ids:
            events.append(row)
            event_ids.add(event_id)

    for rule_id, rule in sorted(accepted_by_id.items()):
        prior = previous_items.get(rule_id, {})
        prior_status = _text(prior.get("status")).upper()
        prior_refs = _dedupe_version_refs(_list(prior.get("source_version_refs")))
        current_refs = _rule_version_refs(rule)
        current_fingerprint = _source_snapshot_fingerprint(rule)
        if not prior:
            reason = "RULE_ACTIVATED_FROM_CURRENT_AUTHORITY"
            from_status = "ABSENT"
        elif prior_status in _TERMINAL_NON_EXECUTABLE:
            reason = "RULE_REACTIVATED_FROM_CURRENT_AUTHORITY"
            from_status = prior_status
        elif _text(prior.get("source_snapshot_fingerprint")) != current_fingerprint:
            reason = "RULE_AUTHORITY_SOURCE_REFRESHED"
            from_status = prior_status or "ACTIVE"
        else:
            reason = "RULE_ACTIVE_RECONFIRMED"
            from_status = prior_status or "ACTIVE"
        lifecycle_event = _event(
            rule_id=rule_id,
            from_status=from_status,
            to_status="ACTIVE",
            reason=reason,
            previous_source_versions=prior_refs,
            current_source_versions=current_refs,
        )
        append_event(lifecycle_event)
        items[rule_id] = {
            "rule_id": rule_id,
            "candidate_id": rule.get("candidate_id"),
            "status": "ACTIVE",
            "execution_allowed": True,
            "reason": reason,
            "source_version_refs": current_refs,
            "source_snapshot_fingerprint": current_fingerprint,
            "rule_snapshot": rule,
            "last_event_id": lifecycle_event["event_id"],
        }

    for rule_id, prior in sorted(previous_items.items()):
        if rule_id in accepted_by_id:
            continue
        prior_status = _text(prior.get("status")).upper() or "ACTIVE"
        if prior_status in _TERMINAL_NON_EXECUTABLE:
            items[rule_id] = prior
            continue
        rule_snapshot = _dict(prior.get("rule_snapshot"))
        reason, current_refs = _stale_reason(
            rule_snapshot,
            inventory=inventory,
            inventory_authoritative=inventory_authoritative,
        )
        prior_refs = _dedupe_version_refs(
            _list(prior.get("source_version_refs")) or _rule_version_refs(rule_snapshot)
        )
        lifecycle_event = _event(
            rule_id=rule_id,
            from_status=prior_status,
            to_status="STALE",
            reason=reason,
            previous_source_versions=prior_refs,
            current_source_versions=current_refs,
        )
        append_event(lifecycle_event)
        items[rule_id] = {
            **prior,
            "rule_id": rule_id,
            "status": "STALE",
            "execution_allowed": False,
            "reason": reason,
            "source_version_refs": prior_refs,
            "current_source_version_refs": current_refs,
            "rule_snapshot": rule_snapshot,
            "last_event_id": lifecycle_event["event_id"],
        }

    rows = [items[key] for key in sorted(items)]
    counts = {
        status: sum(1 for row in rows if _text(row.get("status")).upper() == status)
        for status in ("ACTIVE", "STALE", "REJECTED", "SUPERSEDED")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "items": rows,
        "events": events,
        "active_rule_count": counts["ACTIVE"],
        "stale_rule_count": counts["STALE"],
        "rejected_rule_count": counts["REJECTED"],
        "superseded_rule_count": counts["SUPERSEDED"],
        "source_inventory_authoritative": inventory_authoritative,
        "stale_rules_execute": False,
        "active_rule_library_is_execution_authority": True,
    }


__all__ = [
    "SCHEMA_VERSION",
    "active_source_version_inventory",
    "annotate_rule_candidates_with_source_versions",
    "project_implicit_rule_lifecycle",
]
