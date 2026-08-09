"""Governed-write cleanup and restoration helpers for experiment execution.

Extracted from ``experiment_executor``. Symbols are re-exported from the
executor for compatibility with existing call sites and tests.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _observation_state(value: Any) -> dict[str, Any]:
    row = _dict(value)
    return {
        "status": int(row.get("status") or row.get("status_code") or 0),
        "body": row.get("body"),
    }


def _governance_audit_receipt_id(governed: dict[str, Any]) -> str:
    row = _dict(governed)
    audit_record = _dict(row.get("audit_record"))
    audit_path = _text(row.get("audit_path"))
    if not audit_record and not audit_path:
        return ""
    material = {
        "audit_record": audit_record,
        "audit_path": audit_path,
        "before_ref": _text(row.get("before_ref")),
        "after_ref": _text(row.get("after_ref")),
        "accepted": row.get("accepted") is True,
    }
    return "audit_" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:24]


def _resource_identity_candidates(value: Any) -> set[str]:
    identities: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
                if (
                    normalized in {"id", "uuid", "key"}
                    or normalized.endswith("id")
                ) and not isinstance(child, (dict, list)) and _text(child):
                    identities.add(_text(child))
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return identities


def _primary_resource_identity_candidates(value: Any) -> set[str]:
    row = _dict(value)
    primary = {
        _text(child)
        for key, child in row.items()
        if "".join(ch for ch in str(key).lower() if ch.isalnum())
        in {"id", "uuid", "key"}
        and not isinstance(child, (dict, list))
        and _text(child)
    }
    if primary:
        return primary
    for envelope_key in ("data", "result", "resource", "item", "record"):
        nested = row.get(envelope_key)
        if isinstance(nested, dict):
            nested_primary = _primary_resource_identity_candidates(nested)
            if nested_primary:
                return nested_primary
    return _resource_identity_candidates(value)


def _server_managed_field(value: Any) -> bool:
    normalized = "".join(ch for ch in str(value or "").lower() if ch.isalnum())
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
            key=_canonical_json,
        )
    return value


def _meaningful_observation_state(value: Any) -> dict[str, Any]:
    state = _observation_state(value)
    return {
        "status": state.get("status"),
        "body": _without_server_managed_fields(state.get("body")),
    }


_COLLECTION_WRAPPER_KEYS = ("records", "data", "items", "results", "rows")

# Pagination / list-envelope metadata. Industry-neutral vocabulary for detecting
# a collection wrapper that should unwrap to nested rows — not a primary entity.
_COLLECTION_ENVELOPE_META_KEYS = frozenset({
    "page",
    "pages",
    "total",
    "count",
    "size",
    "limit",
    "offset",
    "next",
    "previous",
    "prev",
    "cursor",
    "has_more",
    "meta",
    "links",
    "pagination",
    "total_count",
    "totalcount",
    "page_size",
    "pagesize",
    "page_number",
    "pagenumber",
    "per_page",
    "perpage",
    "number_of_elements",
    "numberofelements",
})


def _normalized_field_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _dict_has_resource_identity(value: dict[str, Any]) -> bool:
    """True when the object carries a conventional resource identity scalar."""
    for key, child in value.items():
        if (
            isinstance(child, bool)
            or not isinstance(child, (str, int, float))
            or not str(child).strip()
        ):
            continue
        raw_key = str(key).strip()
        normalized = _normalized_field_key(raw_key)
        if (
            normalized in {"id", "uuid", "guid", "key"}
            or raw_key.lower().endswith(("_id", "-id"))
            or raw_key.endswith(("Id", "ID"))
        ):
            return True
    return False


def _dict_has_primary_entity_scalars(
    value: dict[str, Any],
    *,
    skip_key: str,
) -> bool:
    """True when parent has non-envelope business scalars beyond a nested list.

    Distinguishes ``{id, status, discount_amount, items:[...]}`` (primary entity
    with embedded children) from ``{items:[...], total: N}`` (collection envelope).
    """
    for key, child in value.items():
        if key == skip_key:
            continue
        if isinstance(child, (dict, list, bool)) or child is None:
            continue
        normalized = _normalized_field_key(key)
        if (
            normalized in {"id", "uuid", "key"}
            or normalized.endswith("id")
            or normalized in _COLLECTION_ENVELOPE_META_KEYS
            or _server_managed_field(key)
        ):
            continue
        if isinstance(child, (str, int, float)) and str(child).strip():
            return True
    return False


def _entity_rows(value: Any) -> list[dict[str, Any]]:
    """Project an observed body into entity row dicts for field comparison.

    Collection envelopes (``{items: [...], total: N}``) unwrap to nested rows.
    Primary resources that embed a child collection (``{id, status, amounts,
    items: [...]}``) keep the parent row and also expose nested children.
    Silently discarding the parent made ``_governed_write_changed_state`` compare
    only unchanged line items and emit ACCEPTED_WRITE_STATE_UNCHANGED while the
    parent status/amounts actually mutated.
    """
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in _COLLECTION_WRAPPER_KEYS:
            nested = value.get(key)
            if isinstance(nested, list):
                nested_rows = [dict(item) for item in nested if isinstance(item, dict)]
                if _dict_has_resource_identity(value) and _dict_has_primary_entity_scalars(
                    value,
                    skip_key=key,
                ):
                    return [dict(value), *nested_rows]
                return nested_rows
        return [dict(value)]
    return []


def _entity_matches_identity(entity: dict[str, Any], identities: set[str]) -> bool:
    if not identities:
        return True
    for key, value in entity.items():
        if isinstance(value, (dict, list)):
            continue
        normalized = _normalized_field_key(key)
        if (
            normalized in {"id", "uuid", "key"}
            or normalized.endswith("id")
        ) and _text(value) in identities:
            return True
    return any(
        not isinstance(value, (dict, list)) and _text(value) in identities
        for value in entity.values()
    )


def _entity_primary_identity_match(
    entity: dict[str, Any],
    identities: set[str],
) -> bool:
    """True when a primary id/uuid/key scalar matches — not a foreign key."""
    if not identities:
        return False
    for key, value in entity.items():
        if isinstance(value, (dict, list)):
            continue
        if _normalized_field_key(key) in {"id", "uuid", "key"} and _text(value) in identities:
            return True
    return False


def _single_entity_for_restoration(value: Any, identities: set[str]) -> dict[str, Any]:
    matches = [
        row for row in _entity_rows(value)
        if _entity_matches_identity(row, identities)
    ]
    if len(matches) == 1:
        return dict(matches[0])
    # Parent + embedded child may both match via id / order_id. Prefer the
    # primary resource identity so status/amount mutations on the parent are
    # not shadowed by an unchanged line item.
    if len(matches) > 1 and identities:
        primary = [
            row for row in matches
            if _entity_primary_identity_match(row, identities)
        ]
        if len(primary) == 1:
            return dict(primary[0])
    return {}


def _cleanup_restores_mutated_fields(
    original: dict[str, Any],
    cleanup: dict[str, Any],
) -> bool:
    original_before = _observation_state(_dict(original).get("before"))
    cleanup_after = _observation_state(_dict(cleanup).get("after"))
    if not (
        200 <= int(original_before.get("status") or 0) < 300
        and 200 <= int(cleanup_after.get("status") or 0) < 300
    ):
        return False
    write_body = _dict(_dict(original).get("write")).get("body")
    if not isinstance(write_body, dict):
        return False
    identities = _primary_resource_identity_candidates(write_body)
    before_entity = _single_entity_for_restoration(
        original_before.get("body"),
        identities,
    )
    after_entity = _single_entity_for_restoration(
        cleanup_after.get("body"),
        identities,
    )
    if not before_entity or not after_entity:
        return False
    mutated_fields = [
        field
        for field, value in write_body.items()
        if field in before_entity
        and field in after_entity
        and not _server_managed_field(field)
        and not isinstance(value, (dict, list))
        and not isinstance(before_entity.get(field), (dict, list))
        and before_entity.get(field) != value
    ]
    return bool(mutated_fields) and all(
        after_entity.get(field) == before_entity.get(field)
        for field in mutated_fields
    )


def _cleanup_compensates_created_resource(
    original: dict[str, Any],
    cleanup: dict[str, Any],
) -> bool:
    original_row = _dict(original)
    cleanup_row = _dict(cleanup)
    if _text(original_row.get("method")).upper() != "POST":
        return False
    cleanup_path = _text(cleanup_row.get("path"))
    # The identity-bound compensation route itself carries the created resource
    # id (e.g. /api/orders/{id}/cancel after a governed order create), so the
    # materialized path is the authoritative identity source. Request-body
    # identity-like fields (e.g. addressId) are foreign keys, not the created
    # resource identity, and must never be required on the compensation path.
    created_identities = _concrete_path_identity_candidates(cleanup_path) or (
        _primary_resource_identity_candidates(
            _dict(original_row.get("write")).get("body")
        )
    )
    if not created_identities or not any(
        identity and identity in cleanup_path for identity in created_identities
    ):
        return False

    original_before = _observation_state(original_row.get("before"))
    original_after = _observation_state(
        original_row.get("response_bound_after") or original_row.get("after")
    )
    cleanup_before = _observation_state(cleanup_row.get("before"))
    cleanup_after = _observation_state(cleanup_row.get("after"))
    if not (
        200 <= int(original_after.get("status") or 0) < 300
        and 200 <= int(cleanup_before.get("status") or 0) < 300
        and 200 <= int(cleanup_after.get("status") or 0) < 300
    ):
        return False
    if _single_entity_for_restoration(original_before.get("body"), created_identities):
        return False
    if not _single_entity_for_restoration(original_after.get("body"), created_identities):
        return False

    before_entity = _single_entity_for_restoration(
        cleanup_before.get("body"),
        created_identities,
    )
    after_entity = _single_entity_for_restoration(
        cleanup_after.get("body"),
        created_identities,
    )
    if not before_entity or not after_entity:
        return False
    changed_business_fields = [
        field
        for field in before_entity
        if field in after_entity
        and not _server_managed_field(field)
        and not isinstance(before_entity.get(field), (dict, list))
        and not isinstance(after_entity.get(field), (dict, list))
        and before_entity.get(field) != after_entity.get(field)
    ]
    return bool(changed_business_fields)


def _concrete_path_identity_candidates(path: str) -> set[str]:
    """Extract concrete resource tokens from a materialized request path."""
    identities: set[str] = set()
    for segment in _text(path).split("/"):
        token = _text(segment)
        if not token or "{" in token or "}" in token:
            continue
        # Prefer opaque identifiers over static vocabulary segments.
        if any(ch.isdigit() for ch in token) and len(token) >= 6:
            identities.add(token)
    return identities


def _cleanup_recreates_deleted_resource(
    original: dict[str, Any],
    cleanup: dict[str, Any],
) -> bool:
    """Prove DELETE primary was reversed by an accepted recreate write.

    Recreate may mint a new identity, so proof is presence removal then
    restoration of comparable non-identity business fields (or presence alone
    when the recreate response only returns a new id).
    """
    original_row = _dict(original)
    cleanup_row = _dict(cleanup)
    if _text(original_row.get("method")).upper() != "DELETE":
        return False
    if _text(cleanup_row.get("method")).upper() not in {"POST", "PUT", "PATCH"}:
        return False

    original_before = _observation_state(original_row.get("before"))
    original_after = _observation_state(
        original_row.get("response_bound_after") or original_row.get("after")
    )
    cleanup_after = _observation_state(
        cleanup_row.get("response_bound_after") or cleanup_row.get("after")
    )
    if not (
        200 <= int(original_before.get("status") or 0) < 300
        and 200 <= int(cleanup_after.get("status") or 0) < 300
    ):
        return False

    deleted_identities = _concrete_path_identity_candidates(
        _text(original_row.get("path"))
    )
    before_entity = _single_entity_for_restoration(
        original_before.get("body"),
        deleted_identities,
    )
    after_entity = _single_entity_for_restoration(
        original_after.get("body"),
        deleted_identities,
    )
    presence_removed = bool(before_entity) and not bool(after_entity)
    if not presence_removed:
        presence_removed = int(original_after.get("status") or 0) in {404, 410}
    if not presence_removed:
        return False

    recreate_identities = _primary_resource_identity_candidates(
        _dict(cleanup_row.get("write")).get("body")
    )
    restored_entity = _single_entity_for_restoration(
        cleanup_after.get("body"),
        recreate_identities or deleted_identities,
    )
    if not restored_entity and recreate_identities:
        # Collection observers may not echo the new row immediately; an accepted
        # recreate write body still proves the compensating create landed.
        restored_entity = {
            key: value
            for key, value in _dict(cleanup_row.get("write")).get("body").items()
            if not isinstance(value, (dict, list))
        }
    if not restored_entity:
        return False
    if not before_entity:
        return True

    comparable_fields = [
        field
        for field in sorted(set(before_entity).intersection(restored_entity))
        if field in before_entity
        and field in restored_entity
        and not _server_managed_field(field)
        and "".join(ch for ch in str(field).lower() if ch.isalnum())
        not in {"id", "uuid", "key"}
        and not str(field).lower().endswith("id")
        and not isinstance(before_entity.get(field), (dict, list))
        and not isinstance(restored_entity.get(field), (dict, list))
    ]
    if not comparable_fields:
        return True
    return all(
        before_entity.get(field) == restored_entity.get(field)
        for field in comparable_fields
    )


def _governed_write_observed_effect(governed: dict[str, Any]) -> bool:
    """Return whether governed evidence proves a write-side effect.

    Transport acceptance is sufficient, but it is not necessary: some broken
    systems return a rejection status after committing the mutation.  In that
    case two successful governed observations whose business state differs are
    the authority.  Server-managed timestamps alone do not count as an effect.
    """
    row = _dict(governed)
    if row.get("accepted") is True:
        return True
    before = _meaningful_observation_state(row.get("before"))
    after = _meaningful_observation_state(
        row.get("response_bound_after") or row.get("after")
    )
    return bool(
        200 <= int(before.get("status") or 0) < 300
        and 200 <= int(after.get("status") or 0) < 300
        and before != after
    )


def _cleanup_restores_governed_write(
    original: dict[str, Any],
    cleanup: dict[str, Any],
) -> bool:
    original_row = _dict(original)
    cleanup_row = _dict(cleanup)
    if (
        not _governed_write_observed_effect(original_row)
        or cleanup_row.get("accepted") is not True
    ):
        return False
    if not _governance_audit_receipt_id(original_row) or not _governance_audit_receipt_id(cleanup_row):
        return False
    original_before = _observation_state(original_row.get("before"))
    cleanup_after = _observation_state(cleanup_row.get("after"))
    if original_before == cleanup_after:
        return True
    if _cleanup_restores_mutated_fields(original_row, cleanup_row):
        return True
    if _cleanup_compensates_created_resource(original_row, cleanup_row):
        return True
    if _cleanup_recreates_deleted_resource(original_row, cleanup_row):
        return True
    original_method = _text(original_row.get("method")).upper()
    cleanup_path = _text(cleanup_row.get("path"))
    created_identities = _primary_resource_identity_candidates(
        _dict(original_row.get("write")).get("body")
    )
    identity_bound = any(
        identity and identity in cleanup_path for identity in created_identities
    )
    cleanup_before = _observation_state(cleanup_row.get("before"))
    if (
        original_method == "POST"
        and identity_bound
        and 200 <= int(cleanup_before.get("status") or 0) < 300
        and int(cleanup_after.get("status") or 0) in {404, 410}
    ):
        return True
    # ── Presence-removal proof (collection-observed deletes) ──
    # A delete compensator observed through a collection path returns 200 with
    # the surviving rows, never 404. Strict before==after equality then fails
    # whenever the collection drifted between observations (concurrent rows,
    # ordering, timestamps), and the 404 branch can never fire. The cleanup is
    # still proven when the governed before observation contained the created
    # row and the after observation no longer contains it — the run created
    # the row and the run removed it. Absence of the identity in the after
    # body is the removal evidence; a wrong-target delete leaves the row
    # present and stays fail-closed.
    if (
        original_method == "POST"
        and _text(cleanup_row.get("method")).upper() == "DELETE"
        and created_identities
        and 200 <= int(cleanup_before.get("status") or 0) < 300
    ):
        before_entity = _single_entity_for_restoration(
            cleanup_before.get("body"),
            created_identities,
        )
        after_entity = _single_entity_for_restoration(
            cleanup_after.get("body"),
            created_identities,
        )
        if before_entity and not after_entity:
            return True
    return False


def _governed_write_attempts(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _dict(step.get("governance_receipt"))
        for step in steps
        if _text(step.get("phase")) in {"control", "treatment"}
        and isinstance(step.get("governance_receipt"), dict)
    ]


def _governed_write_reached_transport(attempt: dict[str, Any]) -> bool:
    """True only when the governed write HTTP attempt left the harness.

    A governance receipt may exist after a before-GET / identity block while
    ``write_request_attempt_count`` stays 0. That is not target mutation and
    must not seal cleanup transport failure.
    """
    row = _dict(attempt)
    try:
        return int(row.get("write_request_attempt_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def _rejected_writes_left_state_unchanged(
    attempts: list[dict[str, Any]],
) -> bool:
    return bool(attempts) and all(
        attempt.get("accepted") is not True
        and bool(_governance_audit_receipt_id(attempt))
        and _observation_state(attempt.get("before"))
        == _observation_state(attempt.get("after"))
        for attempt in attempts
    )


def _governed_write_changed_state(attempt: dict[str, Any]) -> bool:
    row = _dict(attempt)
    if row.get("accepted") is not True:
        return False
    before_obs = row.get("before")
    after_obs = row.get("response_bound_after") or row.get("after")
    before_state = _observation_state(before_obs)
    after_state = _observation_state(after_obs)
    if before_state.get("status") != after_state.get("status"):
        return True

    write_body = _dict(row.get("write")).get("body")
    if isinstance(write_body, dict):
        identities = _primary_resource_identity_candidates(write_body)
        before_entity = _single_entity_for_restoration(
            before_state.get("body"),
            identities,
        )
        after_entity = _single_entity_for_restoration(
            after_state.get("body"),
            identities,
        )
        if before_entity and after_entity:
            comparable_fields = [
                field
                for field in sorted(set(before_entity).intersection(after_entity))
                if field in before_entity
                and field in after_entity
                and not _server_managed_field(field)
                and not isinstance(before_entity.get(field), (dict, list))
                and not isinstance(after_entity.get(field), (dict, list))
            ]
            if any(
                before_entity.get(field) != after_entity.get(field)
                for field in comparable_fields
            ):
                return True
            if (
                _without_server_managed_fields(before_entity)
                != _without_server_managed_fields(after_entity)
            ):
                return True
            # Matched-subset equality is not proof of unchanged business state.
            # Fall through to full observation compare so embedded-child
            # projections cannot waive a real parent mutation.
        elif identities and bool(before_entity) != bool(after_entity):
            return True

    return _meaningful_observation_state(before_obs) != _meaningful_observation_state(
        after_obs
    )
