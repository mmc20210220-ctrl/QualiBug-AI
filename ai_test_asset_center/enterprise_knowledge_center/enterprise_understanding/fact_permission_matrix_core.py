"""Project source-backed role permissions onto the existing permission matrix.

This is a binding stage, not a second authorization engine. Accepted business facts
remain the semantic authority, while ``permission_matrix`` remains the only input used by
Behavior IR to derive permission relations. A fact is materialized only when actor,
action, resource and an exact current interface binding are all available.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .authorization_semantics import (
    resolve_fact_authorization,
    resolve_fact_authorization_delegation,
)

_PERMISSION_METHOD_ACTIONS: dict[str, frozenset[str]] = {
    "GET": frozenset({"get", "read", "view", "list", "search", "query", "lookup"}),
    "POST": frozenset({
        "post", "create", "add", "submit", "send", "start",
        "approve", "review", "reject", "authorize",
    }),
    "PUT": frozenset({"put", "update", "edit", "modify", "replace"}),
    "PATCH": frozenset({"patch", "update", "edit", "modify", "change"}),
    "DELETE": frozenset({"delete", "remove", "cancel", "revoke"}),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[\s，,。；;：:（）()【】\[\]“”\"'、]+", "", _text(value)).lower()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


@lru_cache(maxsize=1)
def _verb_action_lexicon() -> dict[str, tuple[str, ...]]:
    path = Path(__file__).resolve().parents[2] / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"fact_permission_semantic_lexicon_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    mapping = _dict(payload.get("verb_action_lexicon"))
    return {
        _text(source): tuple(_text(item) for item in _list(targets) if _text(item))
        for source, targets in mapping.items()
        if _text(source)
    }


def _action_tokens(action: Any) -> set[str]:
    value = _norm(action)
    if not value:
        return set()
    tokens = {value}
    for source, targets in _verb_action_lexicon().items():
        members = {_norm(source), *(_norm(item) for item in targets)}
        members.discard("")
        if value in members:
            tokens.update(members)
    return tokens


def _resource_tokens(value: Any) -> set[str]:
    raw = _text(value).casefold()
    return {
        token[:-1] if token.isascii() and token.endswith("s") and len(token) > 3 else token
        for token in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", raw)
        if token
    }


def _fact_coordinates(fact: dict[str, Any]) -> tuple[list[str], str, list[str]]:
    subject = _dict(fact.get("subject"))
    object_part = _dict(fact.get("object"))
    actors = list(dict.fromkeys(
        _text(value)
        for value in _list(subject.get("actor_refs"))
        if _text(value) and _text(value) != "系统"
    ))
    action = _text(
        _dict(fact.get("action")).get("canonical")
        or _dict(fact.get("action")).get("raw")
    )
    resources = list(dict.fromkeys(
        _text(value)
        for value in (
            *_list(subject.get("entity_refs")),
            *_list(object_part.get("entity_refs")),
        )
        if _text(value)
    ))
    return actors, action, resources


def _permission_scope(fact: dict[str, Any]) -> str:
    scope = _dict(fact.get("scope"))
    for key in (
        "ownership", "owner_scope", "data_scope", "resource_scope",
        "tenant_scope", "organization_scope", "department_scope",
        "warehouse_scope", "project_scope", "region_scope",
    ):
        value = _text(scope.get(key))
        if value:
            return value
    return "unspecified"


def _source_identity(fact: dict[str, Any]) -> tuple[str, str]:
    spans = [
        _dict(row) for row in _list(fact.get("source_spans")) if isinstance(row, dict)
    ]
    fact_id = _text(fact.get("fact_id"))
    return (
        _text(spans[0].get("source_id")) if spans else "business_fact_ledger",
        _text(spans[0].get("locator") or spans[0].get("source_locator"))
        if spans
        else fact_id,
    )


def _interface_matches(
    interface: dict[str, Any],
    *,
    action_tokens: set[str],
    resource_tokens: set[str],
) -> bool:
    method = _text(interface.get("method")).upper()
    path = _text(interface.get("path") or interface.get("raw_path"))
    if method not in _PERMISSION_METHOD_ACTIONS or not path:
        return False
    if not action_tokens.intersection(_PERMISSION_METHOD_ACTIONS[method]):
        return False
    interface_resources: set[str] = set()
    for value in (
        *_list(interface.get("entity_refs")),
        *_list(interface.get("tags")),
        interface.get("resource"),
    ):
        interface_resources.update(_resource_tokens(value))
    # Resource identity is exact after normalization. Path fragments, descriptions and
    # substring similarity are not authorization authority.
    return bool(resource_tokens and resource_tokens.intersection(interface_resources))


def _authorization_gap(
    kind: str,
    *,
    fact_id: str,
    source_id: str,
    source_locator: str,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "gap_type": kind,
        "fact_id": fact_id,
        "source_id": source_id,
        "source_locator": source_locator,
        **fields,
    }


def materialize_fact_permission_matrix(asset: dict[str, Any]) -> dict[str, Any]:
    """Bind accepted source permissions to current interfaces and the matrix SSOT."""
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [
        _dict(row)
        for row in _list(ledger.get("items"))
        if isinstance(row, dict) and _text(_dict(row).get("status")).upper() == "ACCEPTED"
    ]
    interfaces = [
        _dict(row) for row in _list(asset.get("interfaces")) if isinstance(row, dict)
    ]
    rows = [
        dict(row)
        for row in _list(asset.get("permission_matrix") or asset.get("permissions"))
        if isinstance(row, dict)
    ]
    seen = {
        _text(row.get("permission_id"))
        for row in rows
        if _text(row.get("permission_id"))
    }
    gaps: list[dict[str, Any]] = []

    delegated_count = 0
    for fact in facts:
        delegation = resolve_fact_authorization_delegation(fact)
        is_delegation = (
            _text(delegation.get("semantic_kind")).upper()
            == "AUTHORIZATION_DELEGATION"
            and delegation.get("authority_declared") is True
        )
        authorization = delegation if is_delegation else resolve_fact_authorization(fact)
        if _text(authorization.get("semantic_kind")).upper() not in {
            "AUTHORIZATION",
            "AUTHORIZATION_DELEGATION",
        }:
            continue
        fact_id = _text(fact.get("fact_id"))
        source_id, source_locator = _source_identity(fact)
        decision = _text(authorization.get("decision")).upper()
        if (
            _text(authorization.get("resolution_status")).upper() != "RESOLVED"
            or decision not in {"ALLOW", "DENY"}
        ):
            if authorization.get("authority_declared") is True:
                gaps.append(_authorization_gap(
                    "authorization_decision_unresolved",
                    fact_id=fact_id,
                    source_id=source_id,
                    source_locator=source_locator,
                ))
            continue

        actors, action, resources = _fact_coordinates(fact)
        if is_delegation:
            actors = [_text(delegation.get("delegatee_role"))]
            if delegation.get("condition_binding_required") is True:
                gaps.append(_authorization_gap(
                    "authorization_delegation_condition_unbound",
                    fact_id=fact_id,
                    source_id=source_id,
                    source_locator=source_locator,
                    delegator_role=_text(delegation.get("delegator_role")),
                    delegatee_role=_text(delegation.get("delegatee_role")),
                    condition_contract=_dict(delegation.get("condition_contract")),
                ))
                continue
        missing = [
            name
            for name, present in (
                ("actor_refs", bool(actors)),
                ("action", bool(action)),
                ("resource_refs", bool(resources)),
            )
            if not present
        ]
        if missing:
            gaps.append(_authorization_gap(
                "authorization_coordinate_incomplete",
                fact_id=fact_id,
                source_id=source_id,
                source_locator=source_locator,
                missing_coordinate_fields=missing,
            ))
            continue

        action_tokens = _action_tokens(action)
        resource_tokens = set().union(*(_resource_tokens(value) for value in resources))
        matched = [
            interface
            for interface in interfaces
            if _interface_matches(
                interface,
                action_tokens=action_tokens,
                resource_tokens=resource_tokens,
            )
        ]
        if not matched:
            gaps.append(_authorization_gap(
                "authorization_interface_binding_unresolved",
                fact_id=fact_id,
                source_id=source_id,
                source_locator=source_locator,
                action=action,
                resource_refs=resources,
            ))
            continue

        for actor in actors:
            for interface in matched:
                method = _text(interface.get("method")).upper()
                path = _text(interface.get("path") or interface.get("raw_path"))
                permission_id = _stable_id(
                    "fact_permission",
                    fact_id,
                    actor,
                    method,
                    path,
                    decision,
                    _text(delegation.get("delegator_role")) if is_delegation else "",
                )
                if permission_id in seen:
                    continue
                seen.add(permission_id)
                rows.append({
                    "permission_id": permission_id,
                    "role": actor,
                    "resource": path,
                    "actions": sorted({method.lower(), *action_tokens}),
                    "decision": "allow" if decision == "ALLOW" else "deny",
                    "scope": _permission_scope(fact),
                    "source_id": source_id,
                    "source_locator": source_locator,
                    "fact_id": fact_id,
                    "interface_id": _text(
                        interface.get("interface_id") or interface.get("operation_id")
                    ),
                    "authorization_derivation": _text(authorization.get("derivation")),
                    **({
                        "authorization_kind": "delegated_permission",
                        "delegator_role": _text(delegation.get("delegator_role")),
                        "delegatee_role": _text(delegation.get("delegatee_role")),
                        "delegation_contract": {
                            "source_backed": True,
                            "fact_id": fact_id,
                            "delegator_role": _text(delegation.get("delegator_role")),
                            "delegatee_role": _text(delegation.get("delegatee_role")),
                            "condition_binding_required": False,
                        },
                    } if is_delegation else {}),
                    "source_backed": True,
                })
                if is_delegation:
                    delegated_count += 1

    existing_gaps = [
        dict(row) for row in _list(asset.get("coverage_gaps")) if isinstance(row, dict)
    ]
    existing_keys = {
        (_text(row.get("gap_type") or row.get("kind")), _text(row.get("fact_id")))
        for row in existing_gaps
    }
    for gap in gaps:
        key = (_text(gap.get("gap_type")), _text(gap.get("fact_id")))
        if key not in existing_keys:
            existing_gaps.append(gap)
            existing_keys.add(key)

    asset["permission_matrix"] = rows
    asset["coverage_gaps"] = existing_gaps
    summary = _dict(asset.get("summary"))
    summary["source_fact_permission_matrix_count"] = sum(
        row.get("source_backed") is True for row in rows
    )
    summary["authorization_binding_gap_count"] = len(gaps)
    summary["source_fact_delegated_permission_count"] = delegated_count
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update({
        "source_fact_authorization_uses_permission_matrix_authority": True,
        "authorization_interface_binding_is_exact": True,
        "authorization_fuzzy_resource_binding_allowed": False,
        "authorization_delegation_uses_distinct_authority": True,
        "conditional_delegation_requires_runtime_binding": True,
        "delegator_is_not_automatically_directly_permitted": True,
    })
    asset["governance"] = governance
    return asset


__all__ = ["materialize_fact_permission_matrix"]
