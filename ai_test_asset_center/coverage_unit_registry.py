"""Coverage Unit facade with exact service and ordered-operation identity.

The current mainline implementation is preserved byte-for-byte in
``coverage_unit_registry_base``.  Actor variants remain intentionally outside
Coverage Unit identity, but two deployment services or two ordered operation
paths are different executable surfaces and must never collapse merely because
their first METHOD/path and assertion shape look alike.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import coverage_unit_registry_base as _base

for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

_ORIGINAL_DERIVE = _base.derive_canonical_obligation_key
_ORIGINAL_ATTACH = _base.attach_canonical_obligation_keys


def _operation_index(
    behavior_ir: dict[str, Any] | None,
    operation_index: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if isinstance(operation_index, dict):
        return operation_index
    if not isinstance(behavior_ir, dict):
        return {}
    return {
        _base._text(row.get("id")): row
        for row in _base._list(behavior_ir.get("operations"))
        if isinstance(row, dict) and _base._text(row.get("id"))
    }


def _ordered_operation_refs(obligation: dict[str, Any]) -> list[str]:
    refs = [
        _base._text(value)
        for value in _base._list(obligation.get("required_operations"))
        if _base._text(value)
    ]
    if refs:
        return refs
    prop = _base._dict(obligation.get("property"))
    refs = [
        _base._text(value)
        for value in _base._list(prop.get("required_operations") or prop.get("operation_refs"))
        if _base._text(value)
    ]
    if refs:
        return refs
    direct = _base._text(prop.get("operation_ref"))
    return [direct] if direct else []


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def ordered_operation_sequence_identity(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any] | None = None,
    operation_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Return identity for ordered multi-operation paths; single-op stays legacy."""
    refs = _ordered_operation_refs(obligation)
    if len(refs) <= 1:
        return ""
    operations = _operation_index(behavior_ir, operation_index)
    sequence: list[str] = []
    for ref in refs:
        operation = _base._dict(operations.get(ref))
        method = _base._text(operation.get("method")).upper()
        path = _base._normalize_operation_path(
            _base._text(operation.get("path") or operation.get("raw_path"))
        )
        sequence.append(f"{method} {path}" if method and path else f"ref:{ref}")
    return _digest({"ordered_operations": sequence})


def service_identity(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any] | None = None,
    operation_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Return deployment-service identity for the obligation's operation path.

    The planner's single-service guard uses ``_service_name`` or ``service``.
    Coverage Unit identity uses the same source fields so grouping cannot cross
    the later execution-scope boundary.  No service declaration means the
    legacy service-agnostic identity is preserved.
    """
    refs = _ordered_operation_refs(obligation)
    if not refs:
        return ""
    operations = _operation_index(behavior_ir, operation_index)
    slots: list[str] = []
    any_declared = False
    for ref in refs:
        operation = _base._dict(operations.get(ref))
        service = _base._text(
            operation.get("_service_name")
            or operation.get("service")
            or operation.get("service_name")
        )
        if service:
            any_declared = True
        slots.append(service or "<unscoped>")
    if not any_declared:
        return ""
    # Keep path position so a cross-service A→B path differs from B→A.
    return ">".join(slots)


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
    service = service_identity(
        obligation,
        behavior_ir=behavior_ir,
        operation_index=operation_index,
    )
    path_identity = ordered_operation_sequence_identity(
        obligation,
        behavior_ir=behavior_ir,
        operation_index=operation_index,
    )
    result["service_identity"] = service
    result["ordered_operation_sequence_identity"] = path_identity
    if not service and not path_identity:
        return result

    canonical_key = _base._text(result.get("canonical_obligation_key"))
    additions: list[str] = []
    if service:
        additions.append(f"service:{service}")
    if path_identity:
        additions.append(f"path:{path_identity}")
    canonical_key = "|".join([part for part in [canonical_key, *additions] if part])
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
    # The base attach function resolves derive_canonical_obligation_key from its
    # module globals. We patch that global below, so every existing field is
    # still produced by the historical implementation and only the two exact
    # identity dimensions are appended here for diagnostics.
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
        row["canonical_obligation_key"] = key["canonical_obligation_key"]
        row["coverage_unit_id"] = key["coverage_unit_id"]
        components = dict(_base._dict(row.get("canonical_key_components")))
        components["service_identity"] = key["service_identity"]
        components["ordered_operation_sequence_identity"] = key[
            "ordered_operation_sequence_identity"
        ]
        row["canonical_key_components"] = components
    return rows


# Base grouping and downstream arm mechanics resolve these globals at call time.
# Redirect only key derivation/annotation; representative choice and actor-arm
# semantics remain untouched.
_base.derive_canonical_obligation_key = derive_canonical_obligation_key
_base.attach_canonical_obligation_keys = attach_canonical_obligation_keys
