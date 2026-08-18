"""Coverage Unit facade with ordered multi-operation path identity."""
from __future__ import annotations

from typing import Any

from . import coverage_unit_registry_base as _base
from .recall_coverage_authority import ordered_operation_sequence_identity

for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

_ORIGINAL_DERIVE = _base.derive_canonical_obligation_key
_ORIGINAL_ATTACH = _base.attach_canonical_obligation_keys


def derive_canonical_obligation_key(
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
    operation_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = dict(
        _ORIGINAL_DERIVE(
            obligation,
            behavior_ir=behavior_ir,
            operation_index=operation_index,
        )
    )
    path_identity = ordered_operation_sequence_identity(
        obligation,
        behavior_ir=behavior_ir,
        operation_index=operation_index,
    )
    result["ordered_operation_sequence_identity"] = path_identity
    if not path_identity:
        return result
    canonical_key = str(result.get("canonical_obligation_key") or "")
    canonical_key = (
        f"{canonical_key}|path:{path_identity}"
        if canonical_key
        else f"path:{path_identity}"
    )
    result["canonical_obligation_key"] = canonical_key
    material = canonical_key + "|" + _base._observation_semantics_guard(obligation)
    result["coverage_unit_id"] = (
        _base._COVERAGE_UNIT_ID_PREFIX + _base._sha256(material)[:20]
    )
    return result


def attach_canonical_obligation_keys(
    obligations: list[dict[str, Any]],
    behavior_ir: dict[str, Any] | None = None,
    operation_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = _ORIGINAL_ATTACH(
        obligations,
        behavior_ir=behavior_ir,
        operation_index=operation_index,
    )
    for source, row in zip(obligations, rows):
        key = derive_canonical_obligation_key(
            source,
            behavior_ir=behavior_ir,
            operation_index=operation_index,
        )
        components = dict(row.get("canonical_key_components") or {})
        components["ordered_operation_sequence_identity"] = key[
            "ordered_operation_sequence_identity"
        ]
        row["canonical_key_components"] = components
    return rows


# Base grouping resolves these globals at runtime. This preserves all existing
# representative, variant and arm behavior; only path equivalence becomes exact.
_base.derive_canonical_obligation_key = derive_canonical_obligation_key
_base.attach_canonical_obligation_keys = attach_canonical_obligation_keys
