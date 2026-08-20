"""Public experiment executor with compiler-sealed actor-plan authority.

The established execution, exploration, Oracle and delivery mechanics live in
``_experiment_executor_mainline_mechanics``. Current compilation already
promotes assertion-local actor exploration metadata into one top-level
``qualibug.actor-execution-plan.v1`` contract and seals it with ``plan_hash``.
Runtime therefore has no reason to trust the historical unsealed assertion
fallback.

Formal execution accepts the compiler-sealed plan or no exploration plan. A
legacy ``_actor_exploration_plan`` found without the sealed top-level contract is
visible drift and blocks execution; ``candidate_ids[0]`` is never promoted to a
source actor merely because it appears first.

The public boundary also resolves source-declared service ownership before the
transport kernel runs. Single-service experiments are routed to the exact
``multi_service.services`` target; graph-backed multi-service experiments reuse
the established approved-target graph authority; non-graph cross-service plans
and invalid declared topologies fail closed instead of being sent to one
arbitrary base URL. URL-shaped OpenAPI service refs are canonicalized only by an
exact, uniquely-owned topology URL match on a routing-only IR projection.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from . import _experiment_executor_mainline_mechanics as _core
from .service_ref_canonicalization import (
    canonicalize_behavior_ir_service_refs,
)
from .service_topology_config_guard import (
    load_guarded_project_service_topology,
)
from .service_topology_execution_authority import (
    blocked_routing_result,
    resolve_experiment_execution_route,
)

_original_actor_execution_plan = _core._actor_execution_plan
_original_execute_one_experiment = _core.execute_one_experiment

# Runtime outcomes produced before continuation starts cannot be inferred from
# the immutable planning bundle. Keep a lossless, campaign-scoped in-memory
# receipt ledger at the public executor boundary. Every initially selected
# identity is recorded as a real terminal result, explicit budget deferral, or
# UNRECEIPTED. This ledger is execution/resume authority, not a UI preview, so
# it must never be clipped by an arbitrary count. Capture closes when the first
# continuation consumer starts; follow-on rounds are then owned directly by the
# continuation engine and are not double-recorded here.
_CONTINUATION_RETRY_REASONS = {
    "BLOCKED_MISSING_BINDING",
    "HARNESS_FAILED",
    "BLOCKED_MISSING_OBSERVER",
    "BLOCKED_CONTROL_ARM_NOT_PROVEN",
    "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE",
}
_CONTINUATION_EXECUTION_RECEIPTS: dict[str, list[dict[str, str]]] = {}
_CONTINUATION_CAPTURE_CLOSED: set[str] = set()
_CONTINUATION_RECEIPT_LOCK = threading.Lock()


def __getattr__(name: str) -> Any:
    # Lazy delegation: the former ``dir(_core)`` wholesale copy enumerated
    # merged __dir__ names (ghost attributes like _exact_secret_preflight)
    # that were not bound on the half-initialized module during the import
    # cycle, breaking every facade import. Delegation resolves names only
    # when actually used.
    if not name.startswith("__"):
        return getattr(_core, name)
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _legacy_actor_plan_present(experiment: dict[str, Any]) -> bool:
    """Whether an unsealed assertion/property actor plan survived compilation."""

    exp = _dict(experiment)
    direct_property = _dict(exp.get("property"))
    if _dict(direct_property.get(_core._LEGACY_ACTOR_PLAN_KEY)):
        return True
    for raw in _list(exp.get("assertions")):
        assertion = _dict(raw)
        if _dict(_dict(assertion.get("property")).get(_core._LEGACY_ACTOR_PLAN_KEY)):
            return True
    return False


def _actor_execution_plan(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Read only a compiler-sealed top-level actor execution plan."""

    exp = _dict(experiment)
    if _dict(exp.get("actor_execution_plan")):
        # Reuse the established schema/hash validator for the modern contract.
        return _original_actor_execution_plan(exp)
    if _legacy_actor_plan_present(exp):
        return {}, "legacy_actor_execution_plan_not_authoritative"
    return {}, ""


def _sync_public_executor_hooks() -> None:
    """Preserve the historical public monkeypatch/injection surface."""

    for name in tuple(getattr(_core, "_HOOK_NAMES", ())):
        value = globals().get(name)
        if value is not None and hasattr(_core, name):
            setattr(_core, name, value)
    public_loader = globals().get("load_actor_tokens")
    if public_loader is not None:
        _core.load_actor_tokens = public_loader


def execute_one_experiment(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _sync_public_executor_hooks()
    experiment = _dict(args[0] if args else kwargs.get("experiment"))
    behavior_ir = _dict(kwargs.get("behavior_ir"))
    project = _text(kwargs.get("project"))
    root = Path(kwargs.get("root") or ".")
    original_base_url = _text(kwargs.get("base_url"))
    runtime_contract = _dict(kwargs.get("runtime_contract"))

    topology, topology_receipt = (
        load_guarded_project_service_topology(project, root)
        if project
        else ({}, {"status": "NOT_APPLICABLE", "reason_code": "", "detail": "project_missing"})
    )
    if _text(topology_receipt.get("status")) == "BLOCKED":
        return blocked_routing_result(
            experiment,
            {
                "schema_version": "qualibug.service-topology-execution-routing.v1",
                "status": "BLOCKED",
                "mode": "topology_invalid",
                "service_refs": [],
                "base_url": original_base_url,
                "reason_code": _text(topology_receipt.get("reason_code")),
                "detail": _text(topology_receipt.get("detail")),
                "service_topology_config_receipt": topology_receipt,
            },
        )

    routing_behavior_ir = canonicalize_behavior_ir_service_refs(
        behavior_ir,
        topology,
    )
    route = resolve_experiment_execution_route(
        experiment=experiment,
        behavior_ir=routing_behavior_ir,
        base_url=original_base_url,
        runtime_contract=runtime_contract,
        topology=topology,
    )
    route["service_topology_config_receipt"] = topology_receipt
    if _text(route.get("status")) != "READY":
        return blocked_routing_result(experiment, route)

    routed_kwargs = dict(kwargs)
    routed_base_url = _text(route.get("base_url")) or original_base_url
    routed_kwargs["base_url"] = routed_base_url
    routed_kwargs["runtime_contract"] = _dict(route.get("runtime_contract"))
    # The batch may have loaded tokens against a different service URL. Let
    # the governed core reload its identity-safe token view for the exact
    # routed target rather than reusing credentials from another service.
    if routed_base_url and routed_base_url != original_base_url:
        routed_kwargs["actor_tokens"] = None

    result = _original_execute_one_experiment(*args, **routed_kwargs)
    output = dict(_dict(result))
    output["service_topology_routing_receipt"] = {
        key: value for key, value in route.items() if key != "runtime_contract"
    }
    return output


def _capture_continuation_execution_receipts(
    *,
    campaign_id: str,
    selected_rows: list[dict[str, Any]],
    batch: dict[str, Any],
) -> None:
    """Capture terminal/deferred/unreceipted outcomes before continuation."""
    campaign = _text(campaign_id)
    if not campaign:
        return

    result_by_id: dict[str, dict[str, Any]] = {}
    for raw in _list(_dict(batch).get("results")):
        if not isinstance(raw, dict):
            continue
        oid = _text(raw.get("obligation_id"))
        if oid:
            result_by_id[oid] = dict(raw)
    deferred_by_id = {
        _text(raw.get("obligation_id")): dict(raw)
        for raw in _list(_dict(batch).get("budget_deferred"))
        if isinstance(raw, dict) and _text(raw.get("obligation_id"))
    }

    captured: list[dict[str, str]] = []
    selected_ids: set[str] = set()
    for raw in selected_rows:
        if not isinstance(raw, dict):
            continue
        oid = _text(raw.get("obligation_id"))
        if not oid:
            continue
        selected_ids.add(oid)
        result = result_by_id.get(oid)
        if result is not None:
            captured.append({
                "obligation_id": oid,
                "experiment_id": _text(
                    result.get("experiment_id") or raw.get("experiment_id")
                ),
                "status": _text(
                    result.get("status") or result.get("execution_status")
                ).upper(),
                "reason_code": _text(
                    result.get("reason_code")
                    or result.get("block_reason")
                    or result.get("failure_reason")
                ),
                "receipt_kind": "TERMINAL_RESULT",
            })
        elif oid in deferred_by_id:
            deferred = deferred_by_id[oid]
            captured.append({
                "obligation_id": oid,
                "experiment_id": _text(
                    deferred.get("experiment_id") or raw.get("experiment_id")
                ),
                "status": "DEFERRED",
                "reason_code": _text(
                    deferred.get("reason_code")
                    or deferred.get("block_reason")
                    or "BUDGET_DEFERRED"
                ),
                "receipt_kind": "BUDGET_DEFERRED",
            })
        else:
            captured.append({
                "obligation_id": oid,
                "experiment_id": _text(raw.get("experiment_id")),
                "status": "UNRECEIPTED",
                "reason_code": "EXECUTION_RECEIPT_MISSING",
                "receipt_kind": "UNRECEIPTED_SELECTED",
            })

    # Preserve any executor result that was not present in selected_rows; it is
    # still a real outcome and may belong to a compiler-expanded identity.
    for oid, result in result_by_id.items():
        if oid in selected_ids:
            continue
        captured.append({
            "obligation_id": oid,
            "experiment_id": _text(result.get("experiment_id")),
            "status": _text(
                result.get("status") or result.get("execution_status")
            ).upper(),
            "reason_code": _text(
                result.get("reason_code")
                or result.get("block_reason")
                or result.get("failure_reason")
            ),
            "receipt_kind": "TERMINAL_RESULT",
        })

    if not captured:
        return
    with _CONTINUATION_RECEIPT_LOCK:
        if campaign in _CONTINUATION_CAPTURE_CLOSED:
            return
        existing = _CONTINUATION_EXECUTION_RECEIPTS.setdefault(campaign, [])
        by_identity = {
            (
                _text(row.get("obligation_id")),
                _text(row.get("experiment_id")),
            ): dict(row)
            for row in existing
            if _text(row.get("obligation_id"))
        }
        for row in captured:
            by_identity[(row["obligation_id"], row["experiment_id"])] = row
        # This is resume authority, not a diagnostic preview. Retain every
        # distinct identity until its owning continuation domain consumes it.
        _CONTINUATION_EXECUTION_RECEIPTS[campaign] = list(by_identity.values())


def consume_continuation_execution_receipts(
    campaign_id: str,
    *,
    allowed_experiment_ids_by_obligation: dict[str, str] | None = None,
    close_capture: bool = True,
) -> list[dict[str, str]]:
    """Consume captured initial outcomes belonging to one experiment domain."""
    campaign = _text(campaign_id)
    if not campaign:
        return []
    allowed = {
        _text(oid): _text(experiment_id)
        for oid, experiment_id in _dict(allowed_experiment_ids_by_obligation).items()
        if _text(oid)
    }

    def _matches(row: dict[str, str]) -> bool:
        oid = _text(row.get("obligation_id"))
        if not allowed:
            return True
        if oid not in allowed:
            return False
        expected_experiment_id = allowed.get(oid, "")
        actual_experiment_id = _text(row.get("experiment_id"))
        if not expected_experiment_id:
            return True
        # A domain with an exact experiment id may consume only that exact
        # receipt. Treating an empty actual id as a wildcard lets a runtime
        # recompile/expansion steal another domain's outcome for the same
        # obligation id. Selected/deferred/unreceipted rows already inherit the
        # plan-row experiment id during capture, so empty here is genuinely
        # unbound evidence and must remain for its owning/legacy consumer.
        return bool(actual_experiment_id) and expected_experiment_id == actual_experiment_id

    with _CONTINUATION_RECEIPT_LOCK:
        if close_capture:
            _CONTINUATION_CAPTURE_CLOSED.add(campaign)
        existing = list(_CONTINUATION_EXECUTION_RECEIPTS.get(campaign, []))
        if not existing:
            return []
        selected = [row for row in existing if _matches(row)]
        remaining = [row for row in existing if not _matches(row)]
        if remaining:
            _CONTINUATION_EXECUTION_RECEIPTS[campaign] = remaining
        else:
            _CONTINUATION_EXECUTION_RECEIPTS.pop(campaign, None)
        return [dict(row) for row in selected]


def consume_continuation_retry_receipts(
    campaign_id: str,
    *,
    allowed_obligation_ids: set[str] | None = None,
    close_capture: bool = True,
) -> list[dict[str, str]]:
    """Compatibility view returning only retry-eligible captured outcomes."""
    allowed = {
        _text(value): ""
        for value in (allowed_obligation_ids or set())
        if _text(value)
    }
    rows = consume_continuation_execution_receipts(
        campaign_id,
        allowed_experiment_ids_by_obligation=allowed,
        close_capture=close_capture,
    )
    return [
        {
            "obligation_id": _text(row.get("obligation_id")),
            "block_reason": _text(row.get("reason_code")),
            "status": _text(row.get("status")),
            "experiment_id": _text(row.get("experiment_id")),
        }
        for row in rows
        if _text(row.get("status")) in {"BLOCKED", "HARNESS_FAILED"}
        and _text(row.get("reason_code")) in _CONTINUATION_RETRY_REASONS
    ]


def clear_continuation_retry_receipts(campaign_id: str) -> None:
    """Release campaign-scoped continuation capture state at finalization."""
    campaign = _text(campaign_id)
    if not campaign:
        return
    with _CONTINUATION_RECEIPT_LOCK:
        _CONTINUATION_EXECUTION_RECEIPTS.pop(campaign, None)
        _CONTINUATION_CAPTURE_CLOSED.discard(campaign)


def execute_selected_experiments(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Execute a batch and expose pre-continuation outcome receipts losslessly."""
    selected_rows = [
        dict(row)
        for row in _list(args[0] if args else kwargs.get("selected"))
        if isinstance(row, dict)
    ]
    result = _core.execute_selected_experiments(*args, **kwargs)
    batch = dict(_dict(result))
    _capture_continuation_execution_receipts(
        campaign_id=_text(kwargs.get("campaign_id")),
        selected_rows=selected_rows,
        batch=batch,
    )
    return batch


# Mechanics functions resolve this authority from their defining-module globals.
_core._actor_execution_plan = _actor_execution_plan

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_actor_execution_plan",
        "execute_one_experiment",
        "execute_selected_experiments",
        "consume_continuation_execution_receipts",
        "consume_continuation_retry_receipts",
        "clear_continuation_retry_receipts",
    }
)
