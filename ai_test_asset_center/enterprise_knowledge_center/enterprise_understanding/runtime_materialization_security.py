"""Security hardening for governed Runtime Materialization drafts.

This layer prevents an unapproved runtime binding from being copied into even a non-sendable draft,
requires an explicit environment identity, and verifies that a test-data binding contains an actual
value source. It does not make any draft executable.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
from typing import Any

from . import runtime_materialization as _base
from . import runtime_materialization_governance as _governance
from .runtime_materialization_messages import materialization_reason_message
from .schema import as_dict, as_list, stable_id, text


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _scrubbed_binding(slot: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "slot_id": text(slot.get("slot_id")),
        "field": text(slot.get("field")),
        "location": text(slot.get("location")).upper(),
        "resolution_status": reason,
        "draft_value_present": False,
        "secret_value_retained": False,
        "value_executed": False,
    }


def install_secure_runtime_value_resolver() -> None:
    """Install an idempotent fail-close wrapper around the internal value resolver."""
    current = _base._resolve_slot
    if getattr(current, "_qualibug_secure_materialization_value_resolver", False):
        return
    original = current

    @wraps(original)
    def guarded(
        plan: dict[str, Any],
        slot: dict[str, Any],
        asset: dict[str, Any],
        contract_id: str,
    ):
        source_kind = text(as_dict(slot.get("value_source")).get("source_kind"))
        if source_kind != "SOURCE_BACKED_SEMANTIC_VALUE":
            matches = [
                row
                for row in _base._input_catalog(asset)
                if _base._binding_matches(row, plan, slot)
            ]
            if len(matches) == 1 and not _base._approved(matches[0]):
                reason = "RUNTIME_MATERIALIZATION_VALUE_BINDING_NOT_APPROVED"
                return (
                    _scrubbed_binding(slot, "BLOCKED_VALUE_BINDING_NOT_APPROVED"),
                    [
                        _base._unknown(
                            contract_id,
                            reason,
                            details={
                                "slot_id": slot.get("slot_id"),
                                "field": slot.get("field"),
                                "location": slot.get("location"),
                            },
                        )
                    ],
                )
        binding, unknowns = original(plan, slot, asset, contract_id)
        required = bool(slot.get("required"))
        if (
            required
            and binding.get("draft_value_present") is True
            and binding.get("draft_value") is None
        ):
            reason = "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_IS_NULL"
            return (
                _scrubbed_binding(slot, "BLOCKED_REQUIRED_VALUE_IS_NULL"),
                [
                    *unknowns,
                    _base._unknown(
                        contract_id,
                        reason,
                        details={
                            "slot_id": slot.get("slot_id"),
                            "field": slot.get("field"),
                            "location": slot.get("location"),
                        },
                    ),
                ],
            )
        return binding, unknowns

    guarded._qualibug_secure_materialization_value_resolver = True  # type: ignore[attr-defined]
    guarded._qualibug_original_runtime_value_resolver = original  # type: ignore[attr-defined]
    _base._resolve_slot = guarded


def _materialization_unknown(
    materialization_id: str, reason: str, **details: Any
) -> dict[str, Any]:
    return {
        "unknown_id": stable_id(
            "runtime_materialization_unknown",
            materialization_id,
            reason,
            details.get("slot_id"),
            details.get("field"),
            details.get("binding_ref"),
        ),
        "kind": reason,
        "reason_code": reason,
        "message": materialization_reason_message(reason),
        "runtime_materialization_ref": materialization_id,
        "blocks_runtime_materialization": True,
        "execution_allowed": False,
        **details,
    }


def _binding_has_value_source(binding: dict[str, Any]) -> bool:
    if text(binding.get("fixture_ref") or binding.get("entity_ref") or binding.get("value_ref")):
        return True
    if "value" in binding and binding.get("value") is not None:
        return _base._safe_literal(binding.get("value"))
    generator = as_dict(binding.get("generator"))
    return bool(text(generator.get("kind") or binding.get("generator_kind")))


def _attach_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        reason = text(row.get("reason_code") or row.get("kind"))
        if reason and not text(row.get("message")):
            row["message"] = materialization_reason_message(reason)
        result.append(row)
    return result


def _post_audit(asset: dict[str, Any], model: dict[str, Any]) -> None:
    unknowns = _dicts(asset.get("runtime_materialization_unknowns"))
    catalog = {
        text(row.get("binding_id")): row
        for row in _base._input_catalog(asset)
        if text(row.get("binding_id"))
    }
    for materialization in _dicts(asset.get("runtime_materializations")):
        materialization_id = text(materialization.get("materialization_id"))
        environment = as_dict(materialization.get("environment_binding"))
        if not text(environment.get("environment_ref")):
            unknowns.append(
                _materialization_unknown(
                    materialization_id,
                    "RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_UNRESOLVED",
                )
            )
        for draft in _dicts(materialization.get("test_data_setup_drafts")):
            if text(draft.get("resolution_status")) != "RESOLVED_TEST_DATA_REFERENCE":
                continue
            binding_ref = text(draft.get("binding_ref"))
            binding = catalog.get(binding_ref) or {}
            if binding and _binding_has_value_source(binding):
                continue
            unknowns.append(
                _materialization_unknown(
                    materialization_id,
                    "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_HAS_NO_VALUE_SOURCE",
                    slot_id=draft.get("slot_ref"),
                    field=draft.get("field"),
                    binding_ref=binding_ref,
                )
            )
    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in _attach_messages(unknowns)
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["runtime_materialization_unknowns"] = all_unknowns
    model["runtime_materialization_unknowns"] = [dict(row) for row in all_unknowns]
    _governance._rebuild_gate(asset, model)


def project_secure_runtime_materializations_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Project governed drafts with value-retention and binding-content hardening."""
    install_secure_runtime_value_resolver()
    _governance.project_governed_runtime_materializations_to_asset(asset, model)
    _post_audit(asset, model)
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_materialization_unapproved_values_are_scrubbed": True,
            "runtime_materialization_required_null_values_block": True,
            "runtime_materialization_test_data_requires_actual_value_source": True,
            "runtime_materialization_environment_identity_required": True,
            "runtime_materialization_unknowns_include_readable_messages": True,
            "runtime_materialization_secure_projection_is_public_authority": True,
        }
    )
    asset["governance"] = governance
    return asset


def build_secure_runtime_materializations_v1(
    asset: dict[str, Any], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return secure drafts without mutating caller-owned asset or model objects.

    This is the package-level builder authority. It runs the same governed and security audits as
    the main asset projection, then returns detached copies of the formal collections.
    """
    working_asset = deepcopy(asset)
    working_model = deepcopy(model)
    project_secure_runtime_materializations_to_asset(working_asset, working_model)
    return (
        deepcopy(_dicts(working_asset.get("runtime_materializations"))),
        deepcopy(_dicts(working_asset.get("runtime_materialization_unknowns"))),
        deepcopy(as_dict(working_asset.get("runtime_materialization_gate"))),
    )


__all__ = [
    "install_secure_runtime_value_resolver",
    "build_secure_runtime_materializations_v1",
    "project_secure_runtime_materializations_to_asset",
]
