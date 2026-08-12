"""Body-reference authority facade with source-backed target lineage.

The target parsing/resolution mechanics live in ``_body_reference_authority_mechanics``.
An explicit body target may name an operation or path; resolving that operation to an
entity is formal only when the operation itself carries an explicit entity_ref or a
non-conflicting relation with source_refs. A relation row with no source evidence is
not enough to turn an operation/path target into entity identity.
"""
from __future__ import annotations

from typing import Any

from . import _body_reference_authority_mechanics as _core

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _operation_entity_refs(
    operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> set[str]:
    """Return only directly declared or source-backed operation/entity links."""

    refs = {
        _text(value)
        for value in [
            *_list(operation.get("entity_refs")),
            operation.get("entity_ref"),
        ]
        if _text(value)
    }
    operation_ref = _text(operation.get("id") or operation.get("operation_id"))
    entity_ids = {
        _text(row.get("id"))
        for row in _list(_dict(behavior_ir).get("entities"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    for raw in _list(_dict(behavior_ir).get("relations")):
        relation = _dict(raw)
        if (
            not _list(relation.get("source_refs"))
            or _text(relation.get("status")) in {"conflicting", "unsupported"}
        ):
            continue
        relation_refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
            _text(relation.get("entity_ref")),
        }
        if operation_ref in relation_refs:
            refs.update(ref for ref in relation_refs if ref in entity_ids)
    return refs


# Target resolution inside the mechanics module resolves this helper dynamically.
_core._operation_entity_refs = _operation_entity_refs
_original_resolve_body_reference = _core.resolve_body_reference


def _normalize_body_path(value: Any) -> str:
    raw = _text(value)
    return raw[2:] if raw.startswith("$.") else raw


def resolve_body_reference(
    operation: dict[str, Any],
    body_path: str,
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    receipt = _original_resolve_body_reference(
        operation, body_path, behavior_ir=behavior_ir
    )
    if _text(receipt.get("status")) == "RESOLVED":
        return receipt
    operation_ref = _text(operation.get("id") or operation.get("operation_id"))
    matches = [
        _dict(row)
        for row in _list(_dict(behavior_ir).get("body_reference_relations"))
        if _text(_dict(row).get("operation_ref")) == operation_ref
        and _normalize_body_path(_dict(row).get("body_path")) == _normalize_body_path(body_path)
        and _text(_dict(row).get("status")) == "RESOLVED"
        and _text(_dict(row).get("target_entity_ref"))
        and _list(_dict(row).get("source_refs"))
    ]
    targets = {_text(row.get("target_entity_ref")) for row in matches}
    if len(matches) != 1 or len(targets) != 1:
        return receipt
    row = matches[0]
    target_entity_ref = next(iter(targets))
    if target_entity_ref not in {
        _text(entity.get("id"))
        for entity in _list(_dict(behavior_ir).get("entities"))
        if isinstance(entity, dict)
    }:
        return receipt
    return {
        "schema_version": _core.SCHEMA_VERSION,
        "status": "RESOLVED",
        "reason_code": "",
        "operation_ref": operation_ref,
        "body_path": _text(body_path),
        "target_entity_ref": target_entity_ref,
        "authorities": [
            "body_reference_relation:" + (_text(row.get("authority")) or "source_backed")
        ],
        "body_reference_relation_id": _text(row.get("id")),
    }


_core.resolve_body_reference = resolve_body_reference

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "_operation_entity_refs",
        "resolve_body_reference",
    }
)
