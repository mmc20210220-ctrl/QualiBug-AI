"""Compile source-declared read interfaces into formal presence obligations.

A source document that declares a GET/HEAD operation the target does not
implement is a documentation/implementation-drift defect. This binding adds one
deduplicated ``interface_contract`` obligation per declared read operation so
the presence protocol can probe it at execution time and turn a framework-level
404 into a customer-deliverable finding (four-link reachability chain).

Scope is read-only (GET/HEAD) by design: a write endpoint cannot be probed for
existence without sending the write. The obligation carries the operation's
own source_refs, so any resulting finding is attribute-when-present — never a
fabricated source.
"""
from __future__ import annotations

import copy
import functools
from typing import Any

from . import discovery_runtime_planning as _planning
from . import obligation_compiler as _compiler
from .formal_interface_presence_surface import (
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
)
from .real_id_resolver_base import normalize_path_placeholders
from .test_obligation import canonical_risk_families, dedupe_obligations, make_obligation

_INSTALL_MARKER = "_qualibug_source_interface_presence_obligation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_obligations_for_source_interface_presence"

_SAFE_METHODS = frozenset({"GET", "HEAD"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _source_refs(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        for ref in _list(_dict(row).get("source_refs")):
            if not isinstance(ref, dict):
                continue
            key = repr(sorted(ref.items()))
            if key in seen:
                continue
            seen.add(key)
            output.append(copy.deepcopy(ref))
    return output[:8]


def _presence_operations(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("method")).upper() in _SAFE_METHODS
        and _text(row.get("path") or row.get("raw_path"))
        and _text(row.get("status")) not in {"conflicting", "unsupported", "unknown"}
    ]


def compile_obligations_with_source_interface_presence(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    baseline = dict(base_compile(behavior_ir, **kwargs))
    operations = _presence_operations(behavior_ir)
    if not operations:
        by_family = dict(baseline.get("by_family") or {})
        for family in canonical_risk_families():
            by_family.setdefault(family, 0)
        baseline["by_family"] = dict(sorted(by_family.items()))
        baseline["source_interface_presence_obligation_receipt"] = {
            "schema_version": "qualibug.source-interface-presence-obligation-binding.v1",
            "status": "NOT_REQUESTED",
            "operation_count": 0,
            "obligation_count": 0,
            "complete_family_vector": True,
            "write_operations_skipped": 0,
        }
        return baseline

    baseline_rows = [
        dict(row) for row in _list(baseline.get("obligations")) if isinstance(row, dict)
    ]
    additions: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for operation in operations:
        operation_ref = _text(operation.get("id"))
        method = _text(operation.get("method")).upper()
        declared_path = _text(operation.get("path") or operation.get("raw_path"))
        if not operation_ref:
            skipped.append({"operation_ref": "", "reason_code": "INTERFACE_PRESENCE_OPERATION_IDENTITY_MISSING"})
            continue
        identity = (method, normalize_path_placeholders(declared_path))
        if identity in seen:
            continue
        seen.add(identity)
        property_spec = {
            "template": PROTOCOL_TEMPLATE,
            "operation_ref": operation_ref,
            "method": method,
            "path": declared_path,
            "interface_presence": True,
        }
        additions.append(make_obligation(
            risk_family=RISK_FAMILY,
            subject_refs=[operation_ref],
            property_spec=property_spec,
            required_operations=[operation_ref],
            required_observers=[OBSERVER_ID],
            cleanup_requirement={
                "required": False,
                "reason": "read_only_interface_presence_probe",
            },
            source_refs=_source_refs(operation),
            confidence=min(float(operation.get("confidence") or 1.0), 1.0),
        ))

    obligations = dedupe_obligations([*baseline_rows, *additions])
    families = {_text(value) for value in canonical_risk_families() if _text(value)}
    families.update(_text(row.get("risk_family")) for row in obligations if _text(row.get("risk_family")))
    families.update(_text(value) for value in dict(baseline.get("by_family") or {}) if _text(value))
    baseline.update({
        "obligations": obligations,
        "obligation_count": len(obligations),
        "by_family": {
            family: sum(1 for row in obligations if _text(row.get("risk_family")) == family)
            for family in sorted(families)
        },
        "source_interface_presence_obligation_receipt": {
            "schema_version": "qualibug.source-interface-presence-obligation-binding.v1",
            "status": "COMPILED" if additions else "BLOCKED",
            "operation_count": len(operations),
            "obligation_count": len(additions),
            "skipped_count": len(skipped),
            "complete_family_vector": True,
            "write_operations_skipped": 0,
        },
    })
    return baseline


def install_source_interface_presence_obligation_binding() -> None:
    if getattr(_planning, _INSTALL_MARKER, False):
        return
    original = getattr(
        _planning, _ORIGINAL_MARKER, _planning.compile_obligations_from_behavior_ir
    )
    setattr(_planning, _ORIGINAL_MARKER, original)

    @functools.wraps(original)
    def compile_with_interface_presence(behavior_ir: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return compile_obligations_with_source_interface_presence(
            behavior_ir, base_compile=original, **kwargs
        )

    _planning.compile_obligations_from_behavior_ir = compile_with_interface_presence
    _compiler.compile_obligations_from_behavior_ir = compile_with_interface_presence
    setattr(_planning, _INSTALL_MARKER, True)


__all__ = [
    "compile_obligations_with_source_interface_presence",
    "install_source_interface_presence_obligation_binding",
]
