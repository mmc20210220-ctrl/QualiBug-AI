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

# Runtime retry evidence produced before continuation starts cannot be inferred
# from the immutable planning bundle. Keep a bounded, campaign-scoped in-memory
# receipt ledger at the public executor boundary so the continuation authority
# can seed retry work from actual execution outcomes instead of guessing from
# compile state. Capture closes when the first continuation consumer starts;
# follow-on retries are then owned directly by that consumer and are not
# double-recorded here.
_CONTINUATION_RETRY_REASONS = {
    "BLOCKED_MISSING_BINDING",
    "HARNESS_FAILED",
    "BLOCKED_MISSING_OBSERVER",
    "BLOCKED_CONTROL_ARM_NOT_PROVEN",
    "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE",
}
_CONTINUATION_RETRY_RECEIPTS: dict[str, list[dict[str, str]]] = {}
_CONTINUATION_CAPTURE_CLOSED: set[str] = set()
_CONTINUATION_RETRY_LOCK = threading.Lock()
_MAX_CONTINUATION_RETRY_RECEIPTS_PER_CAMPAIGN = 2000


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


def _capture_continuation_retry_receipts(
    *,
    campaign_id: str,
    batch: dict[str, Any],
) -> None:
    """Capture retry-eligible pre-continuation execution outcomes."""
    campaign = _text(campaign_id)
    if not campaign:
        return
    rows: list[dict[str, str]] = []
    for raw in _list(_dict(batch).get("results")):
        if not isinstance(raw, dict):
            continue
        oid = _text(raw.get("obligation_id"))
        status = _text(raw.get("status") or raw.get("execution_status")).upper()
        reason = _text(
            raw.get("reason_code")
            or raw.get("block_reason")
            or raw.get("failure_reason")
        )
        if (
            oid
            and status in {"BLOCKED", "HARNESS_FAILED"}
            and reason in _CONTINUATION_RETRY_REASONS
        ):
            rows.append({
                "obligation_id": oid,
                "block_reason": reason,
                "status": status,
            })
    if not rows:
        return
    with _CONTINUATION_RETRY_LOCK:
        if campaign in _CONTINUATION_CAPTURE_CLOSED:
            return
        existing = _CONTINUATION_RETRY_RECEIPTS.setdefault(campaign, [])
        by_id = {
            _text(row.get("obligation_id")): dict(row)
            for row in existing
            if _text(row.get("obligation_id"))
        }
        for row in rows:
            by_id[row["obligation_id"]] = row
        _CONTINUATION_RETRY_RECEIPTS[campaign] = list(by_id.values())[
            -_MAX_CONTINUATION_RETRY_RECEIPTS_PER_CAMPAIGN:
        ]


def consume_continuation_retry_receipts(
    campaign_id: str,
    *,
    allowed_obligation_ids: set[str] | None = None,
    close_capture: bool = True,
) -> list[dict[str, str]]:
    """Consume captured retry receipts belonging to one continuation domain."""
    campaign = _text(campaign_id)
    if not campaign:
        return []
    allowed = {
        _text(value) for value in (allowed_obligation_ids or set()) if _text(value)
    }
    with _CONTINUATION_RETRY_LOCK:
        if close_capture:
            _CONTINUATION_CAPTURE_CLOSED.add(campaign)
        existing = list(_CONTINUATION_RETRY_RECEIPTS.get(campaign, []))
        if not existing:
            return []
        if not allowed:
            selected = existing
            remaining: list[dict[str, str]] = []
        else:
            selected = [
                row
                for row in existing
                if _text(row.get("obligation_id")) in allowed
            ]
            remaining = [
                row
                for row in existing
                if _text(row.get("obligation_id")) not in allowed
            ]
        if remaining:
            _CONTINUATION_RETRY_RECEIPTS[campaign] = remaining
        else:
            _CONTINUATION_RETRY_RECEIPTS.pop(campaign, None)
        return [dict(row) for row in selected]


def clear_continuation_retry_receipts(campaign_id: str) -> None:
    """Release campaign-scoped retry capture state at campaign finalization."""
    campaign = _text(campaign_id)
    if not campaign:
        return
    with _CONTINUATION_RETRY_LOCK:
        _CONTINUATION_RETRY_RECEIPTS.pop(campaign, None)
        _CONTINUATION_CAPTURE_CLOSED.discard(campaign)


def execute_selected_experiments(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Execute a batch and expose pre-continuation retry receipts losslessly."""
    result = _core.execute_selected_experiments(*args, **kwargs)
    batch = dict(_dict(result))
    _capture_continuation_retry_receipts(
        campaign_id=_text(kwargs.get("campaign_id")),
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
        "consume_continuation_retry_receipts",
        "clear_continuation_retry_receipts",
    }
)
