"""Cleanup execution utilities for experiment executor.
Extracted from experiment_executor.py.
"""
from __future__ import annotations

import hashlib, json, re
from typing import Any

SERVER_MANAGED_FIELDS = frozenset({
    "created_at", "createdAt", "updated_at", "updatedAt",
    "timestamp", "created", "modified", "last_modified",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join([prefix, *(_text(p) for p in parts if _text(p))])
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _observation_state(value: Any) -> dict[str, Any]:
    row = _dict(value)
    return {
        "status": int(row.get("status") or row.get("status_code") or 0),
        "body": row.get("body") or row.get("payload") or {},
    }


def _governance_audit_receipt_id(governed: dict[str, Any]) -> str:
    audit = _dict(governed.get("audit_records") or [{}]) if isinstance(governed.get("audit_records"), list) else {}
    return _text(governed.get("audit_receipt_id") or governed.get("audit_path") or "")


def _resource_identity_candidates(value: Any) -> set[str]:
    found: set[str] = set()
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                kl = k.lower()
                if kl == "id" or kl.endswith("_id") or (len(k) > 2 and k.endswith("Id")):
                    if isinstance(v, (str, int)) and v not in (None, ""):
                        found.add(str(v))
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(value)
    return found


def _server_managed_field(value: Any) -> bool:
    name = str(value or "").lower().replace("_", "")
    return name in {f.replace("_", "") for f in SERVER_MANAGED_FIELDS}


def _entity_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("records", "data", "items", "results", "rows", "content"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [dict(item) for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            inner = _entity_rows(nested)
            if inner:
                return inner
    return [dict(value)]


def _entity_matches_identity(entity: dict[str, Any], identities: set[str]) -> bool:
    if not identities:
        return False
    for v in entity.values():
        if str(v) in identities:
            return True
    return False


def _single_entity_for_restoration(value: Any, identities: set[str]) -> dict[str, Any]:
    matches = [row for row in _entity_rows(value) if _entity_matches_identity(row, identities)]
    return dict(matches[0]) if len(matches) == 1 else {}


def _cleanup_restores_mutated_fields(original: dict[str, Any], cleanup: dict[str, Any]) -> bool:
    original_before = _observation_state(_dict(original).get("before"))
    cleanup_after = _observation_state(_dict(cleanup).get("after"))
    if not (200 <= int(original_before.get("status") or 0) < 300 and 200 <= int(cleanup_after.get("status") or 0) < 300):
        return False
    write_body = _dict(_dict(original).get("write")).get("body")
    if not isinstance(write_body, dict):
        return False
    identities = _resource_identity_candidates(write_body)
    before_entity = _single_entity_for_restoration(original_before.get("body"), identities)
    after_entity = _single_entity_for_restoration(cleanup_after.get("body"), identities)
    if not before_entity or not after_entity:
        return False
    mutated_fields = [
        field for field, value in write_body.items()
        if field in before_entity and field in after_entity
        and not _server_managed_field(field)
        and not isinstance(value, (dict, list))
        and not isinstance(before_entity.get(field), (dict, list))
        and before_entity.get(field) != value
    ]
    return bool(mutated_fields) and all(after_entity.get(f) == before_entity.get(f) for f in mutated_fields)


def _cleanup_compensates_created_resource(original: dict[str, Any], cleanup: dict[str, Any]) -> bool:
    if _text(original.get("method")).upper() != "POST":
        return False
    created_identities = _resource_identity_candidates(_dict(original.get("write")).get("body"))
    cleanup_path = _text(cleanup.get("path"))
    if not created_identities or not any(i and i in cleanup_path for i in created_identities):
        return False
    ob = _observation_state(original.get("before"))
    oa = _observation_state(original.get("after"))
    cb = _observation_state(cleanup.get("before"))
    ca = _observation_state(cleanup.get("after"))
    if not (200 <= int(oa.get("status") or 0) < 300 and 200 <= int(cb.get("status") or 0) < 300 and 200 <= int(ca.get("status") or 0) < 300):
        return False
    if _single_entity_for_restoration(ob.get("body"), created_identities):
        return False
    if not _single_entity_for_restoration(oa.get("body"), created_identities):
        return False
    be = _single_entity_for_restoration(cb.get("body"), created_identities)
    ae = _single_entity_for_restoration(ca.get("body"), created_identities)
    if not be or not ae:
        return False
    changed = [f for f in be if f in ae and not _server_managed_field(f) and not isinstance(be.get(f), (dict, list)) and not isinstance(ae.get(f), (dict, list)) and be.get(f) != ae.get(f)]
    return bool(changed)


def _cleanup_restores_governed_write(original: dict[str, Any], cleanup: dict[str, Any]) -> bool:
    if original.get("accepted") is not True or cleanup.get("accepted") is not True:
        return False
    if not _governance_audit_receipt_id(original) or not _governance_audit_receipt_id(cleanup):
        return False
    ob = _observation_state(original.get("before"))
    ca = _observation_state(cleanup.get("after"))
    if ob == ca:
        return True
    if _cleanup_restores_mutated_fields(original, cleanup):
        return True
    if _cleanup_compensates_created_resource(original, cleanup):
        return True
    method = _text(original.get("method")).upper()
    path = _text(cleanup.get("path"))
    ids = _resource_identity_candidates(_dict(original.get("write")).get("body"))
    id_bound = any(i and i in path for i in ids)
    cb = _observation_state(cleanup.get("before"))
    return bool(method == "POST" and id_bound and 200 <= int(cb.get("status") or 0) < 300 and int(ca.get("status") or 0) in {404, 410})


def _governed_write_attempts(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_dict(s.get("governance_receipt")) for s in steps if _text(s.get("phase")) in {"control", "treatment"} and isinstance(s.get("governance_receipt"), dict)]


def _rejected_writes_left_state_unchanged(attempts: list[dict[str, Any]]) -> bool:
    return bool(attempts) and all(a.get("accepted") is not True and bool(_governance_audit_receipt_id(a)) and _observation_state(a.get("before")) == _observation_state(a.get("after")) for a in attempts)


def _governed_write_changed_state(attempt: dict[str, Any]) -> bool:
    row = _dict(attempt)
    if row.get("accepted") is not True:
        return False
    bs = _observation_state(row.get("before"))
    ae = _observation_state(row.get("after"))
    if bs.get("status") != ae.get("status"):
        return True
    wb = _dict(row.get("write")).get("body")
    if isinstance(wb, dict):
        for k, v in wb.items():
            if not _server_managed_field(k) and not isinstance(v, (dict, list)):
                bv = _dict(bs.get("body")).get(k)
                av = _dict(ae.get("body")).get(k)
                if bv != av:
                    return True
    return False
