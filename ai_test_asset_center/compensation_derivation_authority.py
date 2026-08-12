"""Fail-closed authority for schema-derived cleanup compensation.

Route vocabulary such as ``cancel``, ``close`` or ``revoke`` does not prove
that executing the action reverses a create.  The core historical derivation
already gives structural collection DELETE precedence; this boundary keeps only
those DELETE-backed derived relations.  Explicit source ``compensates``
relations are built through their own source relationship path and are not
filtered here.
"""
from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def install_compensation_derivation_authority(core: Any) -> None:
    current = getattr(core, "_derive_compensation_relations", None)
    if not callable(current) or getattr(current, "_qualibug_delete_only", False):
        return

    def _derive_compensation_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
        relations = current(model)
        operations = {
            _text(row.get("id") or row.get("operation_id")): row
            for row in (model.get("operations") or [])
            if isinstance(row, dict) and _text(row.get("id") or row.get("operation_id"))
        }
        governed: list[dict[str, Any]] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            operation_ref = _text(
                relation.get("operation_ref") or relation.get("from_ref")
            )
            operation = operations.get(operation_ref) or {}
            if _text(operation.get("method")).upper() == "DELETE":
                governed.append(relation)
        return governed

    _derive_compensation_relations._qualibug_delete_only = True
    core._derive_compensation_relations = _derive_compensation_relations
