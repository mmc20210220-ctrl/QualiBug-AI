"""Keep unprovable legacy fresh membership from being promoted to exact.

The generic continuation reconstructor can honor a legacy pending *count* and
bounded preview, but when no exact fresh pool existed it cannot prove which
omitted identities originally occupied the hidden tail. The exact engine may
materialize a ``fresh_pending_pool`` while processing that compatibility view;
that must not silently upgrade inferred membership into persistent authority.

Legacy uncertainty is sticky across resume attempts until some upstream plan
provides an actual exact fresh pool. Retry and budget-deferred pools remain exact
and are not modified here.
"""
from __future__ import annotations

from typing import Any


_MARKER = "legacy_fresh_membership_uncertain"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def should_seed_initial_fresh(obligation_plan: dict[str, Any]) -> bool:
    """Return False when a prior legacy fallback already proved uncertainty."""
    plan = _dict(obligation_plan)
    return not bool(plan.get(_MARKER))


def legacy_fresh_uncertainty_active(obligation_plan: dict[str, Any]) -> bool:
    """Recognize sticky uncertainty or a fresh seeding fallback this attempt."""
    plan = _dict(obligation_plan)
    if plan.get(_MARKER):
        return True
    if "fresh_pending_pool" in plan:
        return False
    receipt = _dict(plan.get("fresh_pending_authority_receipt"))
    return _text(receipt.get("status")).upper() == "LEGACY_FALLBACK"


def preserve_legacy_fresh_uncertainty(
    source_plan: dict[str, Any],
    final_plan: dict[str, Any],
) -> dict[str, Any]:
    """Strip engine-created exact-fresh labels when source membership was legacy."""
    source = _dict(source_plan)
    plan = dict(_dict(final_plan))
    if not legacy_fresh_uncertainty_active(source):
        return plan

    engine_created_count = len(
        plan.get("fresh_pending_pool")
        if isinstance(plan.get("fresh_pending_pool"), list)
        else []
    )
    plan.pop("fresh_pending_pool", None)
    plan.pop("fresh_pending_pool_count", None)
    plan.pop("continuation_preview_authority", None)
    plan.pop("continuation_outstanding_count", None)
    plan[_MARKER] = True

    prior_receipt = _dict(source.get("fresh_pending_authority_receipt"))
    plan["fresh_pending_authority_receipt"] = {
        **prior_receipt,
        "schema_version": "qualibug.fresh-pending-authority.v1",
        "status": "LEGACY_FALLBACK",
        "reason": _text(prior_receipt.get("reason"))
        or "legacy_fresh_membership_not_provable",
        "exact_promotion_prevented": True,
        "engine_inferred_fresh_count_not_sealed": engine_created_count,
    }
    return plan


__all__ = [
    "legacy_fresh_uncertainty_active",
    "preserve_legacy_fresh_uncertainty",
    "should_seed_initial_fresh",
]
