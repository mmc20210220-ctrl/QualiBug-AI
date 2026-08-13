"""Unified governed LLM-comprehension authority (observation layer).

The discovery mainline exercises three independent LLM comprehension channels,
each with its own receipt and its own provider-availability check:

1. ``agent_semantic_linker``          — rule → documented interface binding
2. ``_semantic_extraction`` (augment) — open-semantic business-rule recall
3. ``stage_reason_all_v2``            — generative multi-engine hypothesizing

All three are governed and fail-closed on their own. What was missing is a
single observation surface: one provider-availability fact and one receipt that
aggregates the three channels' outcomes, so an operator can see the whole
comprehension funnel in one place instead of three scattered receipts whose
provider checks could drift apart.

This module is an *observation* authority, not a fourth comprehension engine. It
never calls the LLM, never mutates the knowledge asset, and never re-runs a
channel. It only (a) resolves provider availability from one source of truth and
(b) folds the three channels' existing receipts into a single
``qualibug.llm-comprehension-authority.v1`` record. Degradation is never silent:
an unavailable provider, a failed channel, and a channel that was never requested
are all named in the aggregated receipt rather than collapsed into "zero".
"""
from __future__ import annotations

from typing import Any

RECEIPT_SCHEMA = "qualibug.llm-comprehension-authority.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def resolve_provider() -> dict[str, Any]:
    """Single source of truth for LLM provider availability.

    Config-only; no provider request is issued. Matches the ``enabled`` contract
    used by the reasoner and semantic-link channels (base_url + api_key + model
    must all be set). An invalid local config fails safe to unavailable.
    """
    try:
        from .llm_reasoning import ReasoningConfig

        config = ReasoningConfig.from_env()
    except (OSError, TypeError, ValueError) as exc:
        return {
            "available": False,
            "basis": f"provider_config_invalid:{type(exc).__name__}",
            "model": "",
        }
    if config.enabled:
        return {
            "available": True,
            "basis": "configured_provider",
            "model": str(config.model or ""),
        }
    return {
        "available": False,
        "basis": "provider_not_configured",
        "model": str(config.model or ""),
    }


def _recall_summary(asset: dict[str, Any]) -> dict[str, Any]:
    """Fold the semantic-recall (augment) receipts into one summary.

    The real per-source recall funnel lives in ``semantic_extraction_receipts``
    (each ``SemanticExtractionReceipt`` carries ``rule_funnel``). Promotion
    counts come from ``rule_promotion_receipts`` where the build path wrote them;
    the funnel counts (LLM recalled / validated / explicit vs inferred /
    rejection reasons) are always present once the channel ran, so they are the
    honest primary signal.
    """
    gates = _dict(asset.get("rule_promotion_gates"))
    promo_receipts = asset.get("rule_promotion_receipts") or []
    promoted = sum(
        int(_dict(row).get("promoted_count") or 0)
        for row in promo_receipts
        if isinstance(row, dict)
    )
    skipped: dict[str, int] = {}
    for row in promo_receipts:
        if not isinstance(row, dict):
            continue
        for reason, count in (_dict(row.get("skipped_counts"))).items():
            skipped[str(reason)] = skipped.get(str(reason), 0) + int(count or 0)

    mode_receipts = [
        row for row in (asset.get("semantic_extraction_receipts") or [])
        if isinstance(row, dict)
        and _text(row.get("schema_version")) == "qualibug.semantic-rule-extraction-mode.v1"
    ]
    effective_modes = sorted({_text(row.get("effective_mode")) for row in mode_receipts if _text(row.get("effective_mode"))})

    # Aggregate the real funnel across every per-source semantic receipt.
    funnels = [
        _dict(row.get("rule_funnel"))
        for row in (asset.get("semantic_extraction_receipts") or [])
        if isinstance(row, dict) and isinstance(row.get("rule_funnel"), dict)
    ]
    rejected_reasons: dict[str, int] = {}
    for funnel in funnels:
        for reason, count in (_dict(funnel.get("rejected_reason_counts"))).items():
            rejected_reasons[str(reason)] = rejected_reasons.get(str(reason), 0) + int(count or 0)

    has_funnel = bool(funnels)
    return {
        "status": (
            "NOT_REQUESTED"
            if not mode_receipts and not funnels
            else "AUGMENT_ACTIVE"
            if "augment" in effective_modes
            else "SHADOW_OR_OFF"
        ),
        "effective_modes": effective_modes,
        "llm_rule_candidates": sum(
            int(f.get("llm_rule_candidates") or 0) for f in funnels
        ),
        "llm_rule_validation_passed": sum(
            int(f.get("llm_rule_validation_passed") or 0) for f in funnels
        ),
        "llm_rule_validation_rejected": sum(
            int(f.get("llm_rule_validation_rejected") or 0) for f in funnels
        ),
        "explicit_count": sum(int(f.get("explicit_count") or 0) for f in funnels),
        "inferred_count": sum(int(f.get("inferred_count") or 0) for f in funnels),
        "rejected_reason_counts": rejected_reasons,
        "funnel_observed": has_funnel,
        "promoted_rules": promoted,
        "gates_met": bool(gates.get("gates_met")) if gates else None,
        "skipped_counts": skipped,
    }


def _binding_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    if not receipt:
        return {"status": "NOT_REQUESTED", "accepted_edges": 0, "failed_units": 0}
    return {
        "status": _text(receipt.get("status")) or "UNKNOWN",
        "accepted_edges": int(receipt.get("accepted_relationship_count") or 0),
        "failed_units": int(receipt.get("failed_unit_count") or 0),
        "reason_code": _text(receipt.get("reason_code")),
        "degraded_to_source_only": bool(
            receipt.get("semantic_linking_degraded_to_source_only")
        ),
    }


def _depth_summary(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {"status": "NOT_REQUESTED", "hypotheses": 0, "obligations_added": 0}
    return {
        "status": _text(report.get("status")) or "UNKNOWN",
        "hypotheses": int(report.get("hypotheses_generated") or 0),
        "obligations_added": int(report.get("obligations_added") or 0),
        "bridge_funnel": _dict(report.get("bridge_funnel")),
    }


def build_comprehension_authority_receipt(
    *,
    provider: dict[str, Any] | None = None,
    knowledge_asset: dict[str, Any] | None = None,
    semantic_link_receipt: dict[str, Any] | None = None,
    mainline_reasoner_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate the three comprehension channels into one observable receipt.

    Pure aggregation over already-produced receipts; no LLM call, no asset
    mutation. Missing channels are named ``NOT_REQUESTED``, never silently zero.
    """
    provider_fact = provider if isinstance(provider, dict) else resolve_provider()
    recall = _recall_summary(knowledge_asset if isinstance(knowledge_asset, dict) else {})
    binding = _binding_summary(semantic_link_receipt or {})
    depth = _depth_summary(mainline_reasoner_report or {})

    degraded_reasons: list[str] = []
    if not provider_fact.get("available"):
        degraded_reasons.append("provider_unavailable")
    if binding.get("degraded_to_source_only"):
        degraded_reasons.append("semantic_link_degraded")
    if binding.get("status") == "FAILED":
        degraded_reasons.append("semantic_link_failed")
    if depth.get("status") == "FAILED":
        degraded_reasons.append("reasoner_failed")

    return {
        "schema_version": RECEIPT_SCHEMA,
        "provider": {
            "available": bool(provider_fact.get("available")),
            "basis": _text(provider_fact.get("basis")),
            "model": _text(provider_fact.get("model")),
        },
        "recall": recall,
        "binding": binding,
        "depth": depth,
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
    }


__all__ = [
    "RECEIPT_SCHEMA",
    "resolve_provider",
    "build_comprehension_authority_receipt",
]
