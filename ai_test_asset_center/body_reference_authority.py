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
resolve_body_reference = _core.resolve_body_reference

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
