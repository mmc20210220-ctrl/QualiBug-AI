"""Legacy behavior-slice scheduling and ranking.

Moved out of ``v12_pipeline`` so the compatibility wrapper stays a thin
mainline facade. Public tests may still import these symbols from
``v12_pipeline`` via re-export.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .enterprise_campaign import has_real_confirmation_receipt

# Lower rank = higher scheduling priority (account_status / money before transition).
_SELECTION_KIND_RANK: dict[str, int] = {
    "account_status": 0,
    "money": 1,
    "inventory": 2,
    "concurrency": 3,
    "permission": 4,
    "isolation": 5,
    "transition": 6,
    "invariant": 7,
    "dependency": 8,
    "source_observation": 9,
}

_POOL_COLLAPSE_KINDS = frozenset({"permission", "isolation"})
_POOL_PROTECTED_KINDS = frozenset({
    "account_status",
    "money",
    "concurrency",
    "inventory",
    "isolation",
})
# High-value supplementary kinds must not be starved when entity-diversity
# fills the round budget with hundreds of distinct LLM/invariant entities.
_SELECTION_RESERVED_KINDS = frozenset(
    {"account_status", "money", "inventory", "concurrency", "isolation", "permission"}
)
_POOL_ORIGIN_KEEP_RANK: dict[str, int] = {
    "historical_bug": 0,
    "supplementary": 1,
    "state_graph": 2,
    "analyzer": 3,
    "llm_reasoner": 4,
}

SliceReorderHook = Callable[
    [list[dict[str, Any]], Path, str],
    tuple[list[dict[str, Any]], dict[str, Any]],
]
_SLICE_REORDER_HOOK: SliceReorderHook | None = None


def register_slice_reorder_hook(hook: SliceReorderHook | None) -> None:
    """First-class coverage/learning reorder — no monkey-patch of run_v12_pipeline."""
    global _SLICE_REORDER_HOOK
    _SLICE_REORDER_HOOK = hook


def clear_slice_reorder_hook() -> None:
    register_slice_reorder_hook(None)


def _history_item_counts_as_attempted(item: dict[str, Any]) -> bool:
    if has_real_confirmation_receipt(item):
        return True
    return str(item.get("execution_status") or "").strip().lower() == "executed"


def _slice_history(history: list[dict[str, Any]] | None) -> tuple[set[str], set[str]]:
    attempted: set[str] = set()
    confirmed: set[str] = set()
    for item in history or []:
        if not isinstance(item, dict):
            continue
        ledger = item.get("behavior_slice_ledger")
        if isinstance(ledger, dict):
            attempted.update(str(value) for value in ledger.get("attempted_slice_ids", []) if str(value))
            confirmed.update(str(value) for value in ledger.get("confirmed_slice_ids", []) if str(value))
        slice_id = str(item.get("behavior_slice_id") or item.get("source_slice_id") or item.get("slice_id") or "").strip()
        if slice_id and _history_item_counts_as_attempted(item):
            attempted.add(slice_id)
        if slice_id and has_real_confirmation_receipt(item):
            confirmed.add(slice_id)
    return attempted, confirmed


def _selection_result(*, status: str, stop_reason: str, selected: list[dict[str, Any]], pending: list[dict[str, Any]], attempted: set[str], confirmed: set[str], next_round: int | None, selection_mode: str) -> dict[str, Any]:
    return {
        "status": status,
        "stop_reason": stop_reason,
        "selected": selected,
        "selected_slice_ids": [str(item.get("slice_id") or "") for item in selected],
        "next_round": next_round,
        "remaining_slice_count": max(0, len(pending) - len(selected)),
        "attempted_slice_ids": sorted(attempted),
        "confirmed_slice_ids": sorted(confirmed),
        "selection_mode": selection_mode,
    }


def _slice_has_source_executable_route(item: dict[str, Any]) -> bool:
    endpoints = item.get("endpoints") if isinstance(item, dict) else []
    if not isinstance(endpoints, list):
        return False
    return any(str(path or "").strip().startswith("/") for path in endpoints)


def _scenario_selection_score(scenario: Any) -> float:
    score = 0.0
    execution_policy = str(getattr(scenario, "execution_policy", "") or "")
    category = str(getattr(scenario, "category", "") or "")
    severity = str(getattr(scenario, "severity", "") or "")
    evidence_gaps = list(getattr(scenario, "evidence_gaps", []) or [])
    steps = list(getattr(scenario, "steps", []) or [])
    confidence = float(getattr(scenario, "confidence", 0.0) or 0.0)

    if execution_policy == "approved_sandbox_write":
        score += 6.0
    elif execution_policy == "approved_test_write":
        score += 5.0
    elif execution_policy in {"runtime_approved", "safe_read_only"}:
        score += 3.0

    if bool(getattr(scenario, "is_forbidden_path", False)):
        score += 3.0
    if bool(getattr(scenario, "is_boundary_path", False)):
        score += 1.0
    if bool(getattr(scenario, "is_concurrent", False)) or category == "concurrency":
        score += 2.0
    elif category == "state_machine":
        score += 1.5
    elif category == "dependency":
        score += 1.0
    elif category == "source_observation":
        score -= 1.0

    severity_boost = {"P0": 3.0, "P1": 2.0, "P2": 1.0}.get(severity, 0.0)
    score += severity_boost
    score += min(len(steps), 6) * 0.15
    score += min(max(confidence, 0.0), 1.0)
    score -= min(len(evidence_gaps), 4) * 0.5
    return score


def _normalize_selection_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^(\[[^\]]*\]\s*)+", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"'[^']+'", "'<id>'", text)
    text = re.sub(r'"[^"]+"', '"<id>"', text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", text, flags=re.I)
    text = re.sub(r"\b\d{6,}\b", "<id>", text)
    return text.strip()


def _slice_selection_family(item: dict[str, Any]) -> str:
    family = _normalize_selection_family(item.get("_selection_family"))
    if family:
        return family
    entity = str(item.get("entity") or "").strip().lower()
    kind = str(item.get("kind") or "").strip().lower()
    states = ",".join(str(value).strip().lower() for value in (item.get("states") or []) if str(value).strip())
    endpoints = ",".join(str(value).strip().lower() for value in (item.get("endpoints") or []) if str(value).strip())
    return "|".join(part for part in (entity, kind, states, endpoints) if part) or str(item.get("slice_id") or "")


def _slice_selection_entity(item: dict[str, Any]) -> str:
    return str(item.get("entity") or "").strip().lower()


def _prioritize_confirmed_state_variants(
    items: list[dict[str, Any]],
    *,
    confirmed_slice_ids: set[str],
    all_slices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not items or not confirmed_slice_ids or not all_slices:
        return items
    slice_index = {
        str(item.get("slice_id") or ""): item
        for item in all_slices
        if isinstance(item, dict) and str(item.get("slice_id") or "")
    }
    confirmed_families: dict[tuple[str, str], set[str]] = defaultdict(set)
    for slice_id in confirmed_slice_ids:
        confirmed_item = slice_index.get(str(slice_id))
        if not confirmed_item:
            continue
        entity = _slice_selection_entity(confirmed_item)
        kind = str(confirmed_item.get("kind") or "").strip().lower()
        family = _slice_selection_family(confirmed_item)
        if entity and kind and family:
            confirmed_families[(entity, kind)].add(family)
    if not confirmed_families:
        return items
    prioritized: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for item in items:
        entity = _slice_selection_entity(item)
        kind = str(item.get("kind") or "").strip().lower()
        family = _slice_selection_family(item)
        states = [str(value).strip().upper() for value in (item.get("states") or []) if str(value).strip()]
        if states and family and family not in confirmed_families.get((entity, kind), set()):
            prioritized.append(item)
        else:
            deferred.append(item)
    return prioritized + deferred if prioritized else items


def _slice_hypothesis_origin(item: dict[str, Any]) -> str:
    origin = str(item.get("_hypothesis_origin") or "").strip().lower()
    if origin:
        return origin
    if item.get("_historical_bug_id"):
        return "historical_bug"
    for ref in item.get("source_refs") or []:
        if isinstance(ref, dict) and str(ref.get("kind") or "").strip().lower() == "historical_bug":
            return "historical_bug"
    return "state_graph"


def _slice_is_pool_protected(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind in _POOL_PROTECTED_KINDS:
        return True
    if item.get("_historical_bug_id"):
        return True
    if _slice_hypothesis_origin(item) == "historical_bug":
        return True
    for ref in item.get("source_refs") or []:
        if isinstance(ref, dict) and str(ref.get("kind") or "").strip().lower() == "historical_bug":
            return True
    return False


def _slice_route_collapse_key(item: dict[str, Any]) -> tuple[str, str, str, str] | None:
    kind = str(item.get("kind") or "").strip().lower()
    if kind not in _POOL_COLLAPSE_KINDS:
        return None
    method = str(
        item.get("_permission_method")
        or item.get("_bound_method")
        or item.get("method")
        or "GET"
    ).upper()
    path = str(item.get("_permission_path") or item.get("_bound_path") or "").strip().lower()
    if not path:
        endpoints = item.get("endpoints") if isinstance(item.get("endpoints"), list) else []
        for endpoint in endpoints:
            text = str(endpoint or "").strip().lower()
            if text.startswith("/"):
                path = text.split("?", 1)[0]
                break
    if not path:
        return None
    if kind == "permission":
        actor_contract = "|".join(
            [
                str(item.get("_permission_actor") or item.get("_default_actor") or "").strip().lower(),
                str(item.get("_permission_email") or item.get("_default_email") or "").strip().lower(),
                ",".join(
                    sorted(
                        str(value or "").strip().upper()
                        for value in (item.get("_permission_expected_permitted") or [])
                        if str(value or "").strip()
                    )
                ),
            ]
        )
    else:
        actor_contract = "|".join(
            [
                str(item.get("_isolation_owner_role") or "").strip().lower(),
                str(item.get("_isolation_owner_email") or "").strip().lower(),
                str(item.get("_isolation_viewer_role") or "").strip().lower(),
                str(item.get("_isolation_viewer_email") or "").strip().lower(),
                str(item.get("_isolation_mode") or "path").strip().lower(),
                str(item.get("_isolation_query_param") or "").strip().lower(),
            ]
        )
    return (kind, method, path, actor_contract)


def _slice_llm_invariant_collapse_key(item: dict[str, Any]) -> tuple[str, str, tuple[str, ...], str] | None:
    kind = str(item.get("kind") or "").strip().lower()
    if kind != "invariant" or _slice_hypothesis_origin(item) != "llm_reasoner":
        return None
    entity = _slice_selection_entity(item) or "resource"
    endpoints = tuple(
        sorted(
            str(value or "").strip().lower().split("?", 1)[0]
            for value in (item.get("endpoints") or [])
            if str(value or "").strip().startswith("/")
        )
    )
    if not endpoints:
        return None
    # Do not collapse every invariant that happens to touch the same route.
    # Payment, lifecycle, audit, and conservation hypotheses commonly share one
    # endpoint but represent different executable assertions.  Collapse only
    # semantically identical text, while retaining the old endpoint-level
    # fallback for legacy slices with no semantic text at all.  Keep numeric
    # thresholds and amounts intact: normalizing them would erase distinct
    # business assertions such as "<= 100" versus "<= 200".
    semantic = str(
        item.get("_invariant_text")
        or item.get("_selection_family")
        or item.get("_hypothesis_family")
        or ""
    ).strip().lower()
    semantic = re.sub(r"\s+", " ", semantic)
    return (kind, entity, endpoints, semantic)


def _slice_has_actor_credentials(item: dict[str, Any]) -> bool:
    for key in (
        "_permission_email",
        "_default_email",
        "_isolation_viewer_email",
        "_account_status_email",
    ):
        if str(item.get(key) or "").strip():
            return True
    return False


def _slice_pool_keep_score(item: dict[str, Any]) -> tuple[int, int, int, float, str]:
    origin_rank = _POOL_ORIGIN_KEEP_RANK.get(_slice_hypothesis_origin(item), 9)
    has_route = 1 if _slice_has_source_executable_route(item) else 0
    has_creds = 1 if _slice_has_actor_credentials(item) else 0
    priority = float(item.get("priority") or 0.0)
    slice_id = str(item.get("slice_id") or "")
    return (has_route, has_creds, -origin_rank, priority, slice_id)


def _optimize_behavior_slice_pool(slices: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collapse redundant LLM permission/isolation and invariant duplicates.

    Permission/isolation slices are redundant only when route and actor contract
    are both identical. Distinct actors or expected permissions must survive:
    native-login scenarios execute their declared actor, not every configured
    account. Exact duplicates are still collapsed so they cannot starve money,
    concurrency, and historical-bug coverage within the round budget.
    """
    protected: list[dict[str, Any]] = []
    route_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    invariant_groups: dict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    stats = {
        "input": len(slices),
        "protected": 0,
        "collapsed_permission_isolation": 0,
        "collapsed_llm_invariant": 0,
        "output": 0,
    }

    for item in slices:
        if not isinstance(item, dict):
            continue
        if _slice_is_pool_protected(item):
            protected.append(item)
            stats["protected"] += 1
            continue
        route_key = _slice_route_collapse_key(item)
        if route_key is not None:
            route_groups.setdefault(route_key, []).append(item)
            continue
        invariant_key = _slice_llm_invariant_collapse_key(item)
        if invariant_key is not None:
            invariant_groups.setdefault(invariant_key, []).append(item)
            continue
        passthrough.append(item)

    kept: list[dict[str, Any]] = list(protected)
    for group in route_groups.values():
        winner = max(group, key=_slice_pool_keep_score)
        kept.append(winner)
        if len(group) > 1:
            stats["collapsed_permission_isolation"] += len(group) - 1
    for group in invariant_groups.values():
        winner = max(group, key=_slice_pool_keep_score)
        kept.append(winner)
        if len(group) > 1:
            stats["collapsed_llm_invariant"] += len(group) - 1
    kept.extend(passthrough)
    stats["output"] = len(kept)
    return kept, stats


def _selection_kind_rank(item: dict[str, Any]) -> int:
    kind = str(item.get("kind") or "").strip().lower()
    return _SELECTION_KIND_RANK.get(kind, 9)


def _entity_primary_slice_rank(item: dict[str, Any], index: int) -> tuple[int, int]:
    return (_selection_kind_rank(item), index)


def _slice_is_selection_reserved(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    if kind in _SELECTION_RESERVED_KINDS:
        return True
    return _slice_is_pool_protected(item)


def _diverse_slice_batch_core(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Entity/family diversity fill for an already-ordered candidate list."""
    if budget <= 0 or not items:
        return []
    selected: list[dict[str, Any]] = []
    entity_deferred: list[dict[str, Any]] = []
    family_deferred: list[dict[str, Any]] = []
    entity_primary_ids: set[str] = set()
    seen_entities: set[str] = set()
    seen_families: set[str] = set()
    best_entity_items: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}

    for index, item in enumerate(items):
        entity = _slice_selection_entity(item)
        if not entity:
            continue
        candidate = (_entity_primary_slice_rank(item, index), item)
        current = best_entity_items.get(entity)
        if current is None or candidate[0] < current[0]:
            best_entity_items[entity] = candidate

    for item in items:
        entity = _slice_selection_entity(item)
        family = _slice_selection_family(item)
        primary = best_entity_items.get(entity)
        primary_item = primary[1] if primary else None
        primary_id = str(primary_item.get("slice_id") or "") if isinstance(primary_item, dict) else ""
        current_id = str(item.get("slice_id") or "")
        if entity and entity not in seen_entities and primary_id and current_id == primary_id:
            seen_entities.add(entity)
            if primary_id:
                entity_primary_ids.add(primary_id)
            if family:
                seen_families.add(family)
            selected.append(item)
        else:
            entity_deferred.append(item)
        if len(selected) >= budget:
            return selected

    for item in entity_deferred:
        if str(item.get("slice_id") or "") in entity_primary_ids:
            continue
        family = _slice_selection_family(item)
        if family and family not in seen_families:
            seen_families.add(family)
            selected.append(item)
        else:
            family_deferred.append(item)
        if len(selected) >= budget:
            return selected

    for item in family_deferred:
        selected.append(item)
        if len(selected) >= budget:
            break
    return selected


def _take_diverse_slice_batch(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Select a diverse batch, reserving high-value kinds before generic fill.

    Isolation/permission/money probes are few and source-backed, but entity-first
    diversity against hundreds of invariant entities previously exhausted the
    round budget before those kinds were reached. Reserved kinds are drained
    first (still with internal diversity), then the remainder of the budget is
    filled from non-reserved candidates.
    """
    if budget <= 0 or not items:
        return []
    reserved = [item for item in items if isinstance(item, dict) and _slice_is_selection_reserved(item)]
    remainder = [item for item in items if isinstance(item, dict) and not _slice_is_selection_reserved(item)]
    selected = _diverse_slice_batch_core(reserved, budget)
    remaining_budget = budget - len(selected)
    if remaining_budget > 0 and remainder:
        selected.extend(_diverse_slice_batch_core(remainder, remaining_budget))
    return selected


def _rank_behavior_slices_for_selection(slices: list[dict[str, Any]], scenarios: list[Any] | None = None) -> list[dict[str, Any]]:
    from .policy_wiring import get_policy_value

    configured_signals = get_policy_value(
        "discovery",
        "candidate_ranking_signals",
        ["source_strength", "endpoint_executability", "evidence_gap", "historical_yield"],
    )
    ranking_signals = [
        str(item).strip()
        for item in (configured_signals if isinstance(configured_signals, list) else [])
        if str(item).strip()
    ]

    def numeric(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return parsed if math.isfinite(parsed) else 0.0

    def policy_signal(item: dict[str, Any], signal: str, dynamic: float) -> float:
        if signal == "source_strength":
            return float(len(item.get("source_refs") or []))
        if signal == "endpoint_executability":
            return 1.0 if item.get("endpoints") else 0.0
        if signal == "evidence_gap":
            return float(len(item.get("evidence_gaps") or []))
        if signal == "historical_yield":
            return numeric(item.get("historical_yield"))
        if signal == "weakness_recurrence":
            return numeric(item.get("weakness_recurrence"))
        if signal == "cross_industry_recurrence":
            return numeric(item.get("cross_industry_recurrence"))
        if signal == "runtime_executability":
            explicit = item.get("runtime_executability")
            return numeric(explicit) if explicit is not None else (1.0 if math.isfinite(dynamic) else 0.0)
        if signal == "cleanup_risk":
            return -numeric(item.get("cleanup_risk"))
        if signal == "evidence_completion_probability":
            return numeric(item.get("evidence_completion_probability"))
        return 0.0

    scenario_scores: dict[str, float] = {}
    scenario_families: dict[str, str] = {}
    scenario_selection_origins: dict[str, str] = {}
    for scenario in scenarios or []:
        slice_id = str(getattr(scenario, "behavior_slice_id", "") or "").strip()
        if not slice_id:
            continue
        scenario_scores[slice_id] = max(scenario_scores.get(slice_id, float("-inf")), _scenario_selection_score(scenario))
        title_family = _normalize_selection_family(getattr(scenario, "title", "") or getattr(scenario, "description", ""))
        if title_family and slice_id not in scenario_families:
            scenario_families[slice_id] = title_family
        selection_origin = str(getattr(scenario, "selection_origin", "") or "").strip().lower()
        if selection_origin:
            scenario_selection_origins[slice_id] = selection_origin

    def sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        slice_id = str(item.get("slice_id") or "")
        selection_origin = str(item.get("_selection_origin") or "").strip().lower()
        materialized_boost = 1 if selection_origin == "active_slice_fallback_materialized" else 0
        dynamic = scenario_scores.get(slice_id, float("-inf"))
        base = float(item.get("priority") or 0.0)
        kind_rank = _selection_kind_rank(item)
        policy_scores = tuple(policy_signal(item, signal, dynamic) for signal in ranking_signals)
        return (
            materialized_boost,
            dynamic,
            *policy_scores,
            base,
            kind_rank,
            -len(item.get("source_refs") or []),
        )

    ranked = []
    for item in slices:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        slice_id = str(normalized.get("slice_id") or "")
        if slice_id and slice_id in scenario_families:
            normalized["_selection_family"] = scenario_families[slice_id]
        if slice_id and slice_id in scenario_selection_origins:
            normalized["_selection_origin"] = scenario_selection_origins[slice_id]
        ranked.append(normalized)
    def descending_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        values = sort_key(item)
        numeric_prefix = values[: 2 + len(ranking_signals) + 1]
        kind_rank = values[-2]
        source_ref_rank = values[-1]
        return (
            *(-numeric(value) for value in numeric_prefix),
            kind_rank,
            source_ref_rank,
            str(item.get("entity") or ""),
            str(item.get("slice_id") or ""),
        )

    ranked.sort(key=descending_sort_key)
    return ranked


def _schedule_behavior_slices(
    slices: list[dict[str, Any]],
    settings: dict[str, int],
    history: list[dict[str, Any]] | None,
    *,
    project: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    steered_slices = list(slices)
    coverage_steering: dict[str, Any] = {}
    project_id = str(project or "").strip()
    if _SLICE_REORDER_HOOK is not None and project_id:
        steered_slices, coverage_steering = _SLICE_REORDER_HOOK(
            steered_slices,
            Path(root) if root is not None else Path.cwd(),
            project_id,
        )

    attempted, confirmed = _slice_history(history)
    all_slices = [item for item in steered_slices if isinstance(item, dict) and str(item.get("slice_id") or "")]
    pending = [item for item in all_slices if str(item["slice_id"]) not in confirmed]
    round_number, round_limit, budget = int(settings["round_number"]), int(settings["round_limit"]), int(settings["slice_budget"])
    if not all_slices:
        result = _selection_result(status="stopped", stop_reason="no_source_bound_behavior_slices", selected=[], pending=[], attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="none")
    elif not pending:
        result = _selection_result(status="stopped", stop_reason="all_source_bound_slices_confirmed", selected=[], pending=[], attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="none")
    elif round_number > round_limit:
        result = _selection_result(status="stopped", stop_reason="configured_round_limit_reached", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="round_limit")
    else:
        unattempted = [item for item in pending if str(item["slice_id"]) not in attempted]
        if attempted:
            executable_pending = [item for item in pending if _slice_has_source_executable_route(item)]
            executable_unattempted = [item for item in unattempted if _slice_has_source_executable_route(item)]
            executable_pending = _prioritize_confirmed_state_variants(executable_pending, confirmed_slice_ids=confirmed, all_slices=all_slices)
            executable_unattempted = _prioritize_confirmed_state_variants(executable_unattempted, confirmed_slice_ids=confirmed, all_slices=all_slices)
            if executable_unattempted:
                selected = _take_diverse_slice_batch(executable_unattempted, budget)
                remaining = len(executable_unattempted) - len(selected)
                result = _selection_result(status="planned", stop_reason="slice_budget_reached" if remaining else "selected_final_unattempted_slice_batch", selected=selected, pending=executable_unattempted, attempted=attempted, confirmed=confirmed, next_round=round_number + 1 if remaining and round_number < round_limit else None, selection_mode="next_unattempted_executable_after_history")
            elif unattempted:
                result = _selection_result(status="stopped", stop_reason="remaining_unattempted_slices_not_source_executable", selected=[], pending=unattempted, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="history_exhausted")
            elif executable_pending:
                selected = _take_diverse_slice_batch(executable_pending, budget)
                remaining = len(executable_pending) - len(selected)
                result = _selection_result(status="planned", stop_reason="slice_budget_reached" if remaining else "selected_retryable_executable_slice_batch", selected=selected, pending=executable_pending, attempted=attempted, confirmed=confirmed, next_round=round_number + 1 if remaining and round_number < round_limit else None, selection_mode="retry_executable_after_history")
            else:
                result = _selection_result(status="stopped", stop_reason="all_pending_slices_attempted_needs_new_evidence_or_policy", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="history_exhausted")
        else:
            offset = (round_number - 1) * budget
            # Prioritize slices with source-bound executable routes on first round too.
            # Without this filter, route-less slices (DB-only entities) generate
            # scenarios with empty steps → 404 false positives.
            _candidates = [item for item in pending if _slice_has_source_executable_route(item)] or pending
            selected = _take_diverse_slice_batch(_candidates[offset:], budget)
            if not selected:
                result = _selection_result(status="stopped", stop_reason="no_remaining_slice_in_configured_round", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="round_paging")
            else:
                remaining = len(pending) - offset - len(selected)
                result = _selection_result(status="planned", stop_reason="slice_budget_reached" if remaining else "selected_final_available_slice_batch", selected=selected, pending=pending[offset:], attempted=attempted, confirmed=confirmed, next_round=round_number + 1 if remaining and round_number < round_limit else None, selection_mode="round_paging")

    if coverage_steering:
        result["coverage_steering"] = coverage_steering
        if coverage_steering.get("status") == "applied":
            mode = str(result.get("selection_mode") or "")
            if "coverage_learning_steered" not in mode:
                result["selection_mode"] = (
                    f"{mode}+coverage_learning_steered" if mode else "coverage_learning_steered"
                )
    return result
