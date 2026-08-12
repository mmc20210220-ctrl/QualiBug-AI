"""Bind runtime materialization cleanup to the obligation's own write authority.

The historical materializer selected the first DELETE operation in the whole
Behavior IR when it needed an API compensator.  That made an unrelated route a
valid cleanup candidate purely because it appeared first.  This authority keeps
the materializer's implementation intact but scopes cleanup resolution to the
current obligation and the source-backed cleanup_operation_authority.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_BEHAVIOR_IR: ContextVar[dict[str, Any] | None] = ContextVar(
    "qualibug_runtime_materialization_behavior_ir", default=None
)


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _t(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Any) -> list[str]:
    return list(dict.fromkeys(_t(value) for value in _l(values) if _t(value)))


def install_runtime_materialization_cleanup_authority(module: Any) -> None:
    if getattr(module, "_qualibug_cleanup_authority_installed", False):
        return

    original_planning = module._resolve_planning_materialization

    def resolve_planning_materialization(*, behavior_ir, **kwargs):
        token = _BEHAVIOR_IR.set(_d(behavior_ir))
        try:
            return original_planning(behavior_ir=behavior_ir, **kwargs)
        finally:
            _BEHAVIOR_IR.reset(token)

    def resolve_cleanup_authority(
        *,
        obligation: dict[str, Any],
        ops: dict[str, dict[str, Any]],
        available_adapters: Any,
        reason: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        unresolved: list[dict[str, Any]] = []
        cleanup_req = obligation.get("cleanup_requirement")
        required = bool(_d(cleanup_req).get("required")) or _t(cleanup_req).lower() in {
            "required", "true", "1", "yes"
        }
        if reason in {
            "BLOCKED_NON_REVERSIBLE_WRITE",
            "BLOCKED_INVALID_CLEANUP_PLAN",
            "BLOCKED_STEP_CLEANUP_UNCOVERED",
        }:
            required = True
        if not required and reason not in {
            "BLOCKED_NON_REVERSIBLE_WRITE",
            "BLOCKED_INVALID_CLEANUP_PLAN",
            "BLOCKED_STEP_CLEANUP_UNCOVERED",
        }:
            return {"required": False, "authority_resolved": True, "tier": ""}, unresolved

        prop = _d(obligation.get("property"))
        primary_ref = _t(prop.get("operation_ref"))
        if not primary_ref:
            refs = [ref for ref in _unique(obligation.get("required_operations")) if ref in ops]
            primary_ref = refs[0] if len(refs) == 1 else ""
        primary_op = _d(ops.get(primary_ref))
        if not primary_ref or not primary_op:
            reason_code = "CLEANUP_PRIMARY_OPERATION_UNRESOLVED"
            if required:
                unresolved.append({
                    "kind": "cleanup",
                    "ref": _t(obligation.get("obligation_id")),
                    "reason": reason_code,
                })
            return {
                "required": required,
                "authority_resolved": not required,
                "tier": "",
                "reason_code": reason_code if required else "",
            }, unresolved

        behavior_ir = _d(_BEHAVIOR_IR.get())
        if not behavior_ir:
            behavior_ir = {"operations": list(ops.values()), "relations": [], "entities": []}

        cleanup_receipt: dict[str, Any] = {}
        try:
            from .cleanup_operation_authority import resolve_cleanup_operation

            cleanup_receipt = _d(
                resolve_cleanup_operation(primary_op, behavior_ir=behavior_ir)
            )
        except Exception as exc:
            reason_code = f"CLEANUP_OPERATION_AUTHORITY_ERROR:{type(exc).__name__}"
            unresolved.append({
                "kind": "cleanup",
                "ref": _t(obligation.get("obligation_id")),
                "reason": reason_code,
            })
            return {
                "required": True,
                "authority_resolved": False,
                "tier": "",
                "reason_code": reason_code,
            }, unresolved

        api_compensator = (
            _d(cleanup_receipt.get("cleanup_operation"))
            if _t(cleanup_receipt.get("status")) == "RESOLVED"
            else None
        )

        entity: dict[str, Any] = {}
        entity_refs = list(dict.fromkeys(
            _t(value) for value in _l(primary_op.get("entity_refs")) if _t(value)
        ))
        if len(entity_refs) == 1:
            matches = [
                _d(row)
                for row in _l(behavior_ir.get("entities"))
                if _t(_d(row).get("id")) == entity_refs[0]
            ]
            if len(matches) == 1:
                entity = matches[0]

        try:
            from .cleanup_adapter_ladder import resolve_cleanup_adapter

            ladder = resolve_cleanup_adapter(
                available_adapters=available_adapters or {"http_api"},
                api_compensator=api_compensator,
                ui_cleanup_declared=False,
                entity=entity,
                availability_only=True,
                target_write_approved=True,
            )
        except Exception as exc:
            unresolved.append({
                "kind": "cleanup",
                "ref": _t(obligation.get("obligation_id")),
                "reason": f"CLEANUP_LADDER_ERROR:{type(exc).__name__}",
            })
            return {"required": True, "authority_resolved": False}, unresolved

        if _t(ladder.get("tier")):
            selected_ref = _t(_d(ladder.get("plan")).get("operation_ref"))
            return {
                "required": True,
                "authority_resolved": True,
                "tier": _t(ladder.get("tier")),
                "plan": _d(ladder.get("plan")),
                "cleanup_operation_refs": [selected_ref] if selected_ref else [],
                "cleanup_authority": _t(cleanup_receipt.get("authority")),
                "availability_only": True,
                "established_before_concrete_compile": True,
            }, unresolved

        if not required:
            return {"required": False, "authority_resolved": True, "tier": ""}, unresolved

        reason_code = (
            _t(cleanup_receipt.get("reason_code"))
            or _t(ladder.get("reason_code"))
            or "CLEANUP_AUTHORITY_UNRESOLVED"
        )
        unresolved.append({
            "kind": "cleanup",
            "ref": _t(obligation.get("obligation_id")),
            "reason": reason_code,
            "detail": _t(ladder.get("detail")),
        })
        return {
            "required": True,
            "authority_resolved": False,
            "reason_code": reason_code,
            "cleanup_operation_refs": [
                _t(value)
                for value in _l(cleanup_receipt.get("candidate_operation_ids"))
                if _t(value)
            ],
        }, unresolved

    module._resolve_cleanup_authority = resolve_cleanup_authority
    module._resolve_planning_materialization = resolve_planning_materialization
    module._qualibug_cleanup_authority_installed = True
