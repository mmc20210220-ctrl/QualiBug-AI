"""Authority facade for lossless semantic-link scheduling.

The mature semantic linker keeps bounded provider units. This facade turns those
unit budgets into explicit paging boundaries so a source asset is never silently
truncated before semantic linking.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from copy import deepcopy
import threading
from typing import Any

from . import agent_semantic_linker as _impl
from .enterprise_knowledge_center._linking import _relationship_is_authoritative


def _text(value: Any) -> str:
    """Local text helper.

    The supporting-fact closure loop below has referenced ``_text`` since it
    was extracted, but no definition existed in this module — global lookup
    does NOT fall through to ``_impl`` (module ``__getattr__`` only serves
    attribute access), so the big-fact-pool path carried a latent NameError
    and could never execute. Delegating to the implementation's helper keeps
    one definition.
    """
    return str(value or "").strip()

RECEIPT_SCHEMA = _impl.RECEIPT_SCHEMA
PROMPT_PROTOCOL = _impl.PROMPT_PROTOCOL
AgentSemanticLinkerError = _impl.AgentSemanticLinkerError

_RULE_BATCH_WINDOW = max(1, int(_impl.MAX_RULES_PER_REQUEST) * int(_impl.MAX_PROVIDER_REQUESTS))
_TRANSITION_BATCH_WINDOW = max(1, int(_impl.MAX_TRANSITIONS_PER_REQUEST))
_CANDIDATE_BATCH_WINDOW = max(1, int(_impl.MAX_CANDIDATES_PER_RULE))
_FACT_BATCH_WINDOW = max(1, int(_impl.MAX_SUPPORTING_FACTS_PER_RULE))

#: Run-level ceiling on provider windows the linker may spend in one scan.
#: Measured 2026-08-23 (CMP_77d5dfe1): an unbounded chunk loop burned the whole
#: 5M-token run budget inside paged enrichment and still failed — every later
#: LLM consumer then failed fast and the spend bought nothing. A declared bound
#: with a named reason code replaces silent unbounded paging; 0 = unlimited is
#: an explicit operator choice, never a hidden default.
_LINKER_WINDOW_BUDGET_ENV = "QUALIBUG_AGENT_LINKER_MAX_WINDOWS"


def _linker_max_windows() -> int:
    raw = str(os.environ.get(_LINKER_WINDOW_BUDGET_ENV) or "").strip()
    if not raw:
        return 24
    try:
        value = int(raw)
    except ValueError:
        return 24
    return value if value >= 0 else 24

_TRANSITION_ROWS_OVERRIDE: ContextVar[tuple[dict[str, Any], ...] | None] = ContextVar(
    "qualibug_transition_rows_override",
    default=None,
)
_FACT_ROWS_OVERRIDE: ContextVar[tuple[dict[str, Any], ...] | None] = ContextVar(
    "qualibug_fact_rows_override",
    default=None,
)

#: Run-level ceiling on provider windows the linker may spend in one enrichment
#: pass. Measured 2026-08-23 (CMP_77d5dfe1): an unbounded closure loop burned
#: the whole 5M-token run budget inside paged enrichment and still failed, so
#: every later LLM consumer failed fast and the spend bought nothing. The cell
#: ([remaining, limit]) is installed once per top-level enrichment and consumed
#: by every paging layer (rule chunks, supporting-fact windows, candidate
#: cascades) — a declared bound with a named reason code, never silent
#: truncation. ``None`` means unlimited, which is only ever an explicit
#: operator choice (QUALIBUG_AGENT_LINKER_MAX_WINDOWS=0), never a default.
_LINKER_WINDOW_BUDGET: ContextVar[tuple[list[int], int] | None] = ContextVar(
    "qualibug_linker_window_budget",
    default=None,
)
LINKER_WINDOW_BUDGET_EXHAUSTED = "AGENT_LINKER_WINDOW_BUDGET_EXHAUSTED"


def _linker_budget_install(limit: int) -> None:
    _LINKER_WINDOW_BUDGET.set(None if limit <= 0 else [[limit, limit], limit])


def _linker_budget_remaining() -> int:
    cell = _LINKER_WINDOW_BUDGET.get()
    return cell[0][0] if cell is not None else -1


def _linker_budget_available() -> bool:
    cell = _LINKER_WINDOW_BUDGET.get()
    return cell is None or cell[0][0] > 0


def _linker_budget_consume() -> None:
    cell = _LINKER_WINDOW_BUDGET.get()
    if cell is not None and cell[0][0] > 0:
        cell[0][0] -= 1


def _linker_budget_used() -> int:
    cell = _LINKER_WINDOW_BUDGET.get()
    return cell[0][1] - cell[0][0] if cell is not None else 0

_original_asset_transition_rows = getattr(
    _impl._asset_transition_rows,
    "_qualibug_authority_original",
    _impl._asset_transition_rows,
)
_original_all_fact_rows = getattr(
    _impl._all_fact_rows,
    "_qualibug_authority_original",
    _impl._all_fact_rows,
)
_original_recall_supporting_facts = getattr(
    _impl._recall_supporting_facts,
    "_qualibug_authority_original",
    _impl._recall_supporting_facts,
)
_original_recall_candidate_interfaces = getattr(
    _impl._recall_candidate_interfaces,
    "_qualibug_authority_original",
    _impl._recall_candidate_interfaces,
)


def _authority_asset_transition_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    override = _TRANSITION_ROWS_OVERRIDE.get()
    if override is not None:
        return [dict(row) for row in override]
    return _original_asset_transition_rows(asset)


def _authority_all_fact_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    override = _FACT_ROWS_OVERRIDE.get()
    if override is not None:
        return [dict(row) for row in override]
    return _original_all_fact_rows(asset)


def _authority_recall_supporting_facts(
    ctx: dict[str, Any],
    fact_pool: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep zero-overlap facts inside the bounded recall window.

    The core scorer is intentionally precision-biased inside the window: facts
    with lexical/structured overlap are ranked first. That is safe only if the
    remaining window slots are still populated. Otherwise a decisive fact with
    no shared vocabulary is discarded before the model sees it, and the later
    contract validator cannot recover it because its fact_id is absent from
    the allowed evidence refs.

    Paging turns MAX_SUPPORTING_FACTS_PER_RULE into a window size. Filling the
    unused slots with the otherwise-unselected facts makes that window lossless;
    the authority layer can then move to the next source window when the rule
    remains unresolved.
    """
    selected = _original_recall_supporting_facts(ctx, fact_pool)
    budget = max(1, int(_impl.MAX_SUPPORTING_FACTS_PER_RULE))
    target_size = min(budget, len(fact_pool))
    if len(selected) >= target_size:
        return selected[:budget]

    selected_ids = {
        str(fact.get("fact_id") or "").strip()
        for fact in selected
        if str(fact.get("fact_id") or "").strip()
    }
    for fact in fact_pool:
        fact_id = str(fact.get("fact_id") or "").strip()
        if fact_id and fact_id in selected_ids:
            continue
        selected.append(dict(fact))
        if fact_id:
            selected_ids.add(fact_id)
        if len(selected) >= target_size:
            break
    return selected[:budget]


def _authority_recall_candidate_interfaces(
    ctx: dict[str, Any],
    interface_signals: dict[str, dict[str, Any]],
    asset: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Fill unused candidate-window slots with otherwise-unscored interfaces.

    Candidate paging makes MAX_CANDIDATES_PER_RULE a source-interface window size.
    The core scorer can still return only three or fewer interfaces when several
    interfaces have deterministic signals and another valid interface has no
    signal at all. Without this fill, that zero-score interface is in exactly one
    source window and is permanently invisible to Tier 2. Filling the remaining
    slots makes each interface window lossless; the LLM remains bounded by the
    same candidate budget and later windows cover the rest of the catalog.
    """
    ranked, stats = _original_recall_candidate_interfaces(
        ctx,
        interface_signals,
        asset,
    )
    ranked = list(ranked)
    budget = max(1, int(_impl.MAX_CANDIDATES_PER_RULE))
    target_size = min(budget, len(interface_signals))
    if len(ranked) >= target_size:
        return ranked[:budget], stats

    channels = {
        str(interface_id): list(channel_names)
        for interface_id, channel_names in (stats.get("channels") or {}).items()
    }
    selected_ids = set(ranked)
    for interface_id in sorted(interface_signals):
        if interface_id in selected_ids:
            continue
        ranked.append(interface_id)
        selected_ids.add(interface_id)
        channels.setdefault(interface_id, [])
        if "window_fill" not in channels[interface_id]:
            channels[interface_id].append("window_fill")
        if len(ranked) >= target_size:
            break

    merged_stats = dict(stats)
    merged_stats["channels"] = {
        interface_id: sorted(set(channel_names))
        for interface_id, channel_names in channels.items()
        if interface_id in ranked
    }
    merged_stats["window_fill"] = len(ranked) > len(stats.get("channels") or {})
    return ranked[:budget], merged_stats


_authority_asset_transition_rows._qualibug_authority_original = _original_asset_transition_rows
_authority_all_fact_rows._qualibug_authority_original = _original_all_fact_rows
_authority_recall_supporting_facts._qualibug_authority_original = _original_recall_supporting_facts
_authority_recall_candidate_interfaces._qualibug_authority_original = _original_recall_candidate_interfaces
if not getattr(_impl._asset_transition_rows, "_qualibug_authority_wrapper", False):
    _authority_asset_transition_rows._qualibug_authority_wrapper = True
    _impl._asset_transition_rows = _authority_asset_transition_rows
if not getattr(_impl._all_fact_rows, "_qualibug_authority_wrapper", False):
    _authority_all_fact_rows._qualibug_authority_wrapper = True
    _impl._all_fact_rows = _authority_all_fact_rows
if not getattr(_impl._recall_supporting_facts, "_qualibug_authority_wrapper", False):
    _authority_recall_supporting_facts._qualibug_authority_wrapper = True
    _impl._recall_supporting_facts = _authority_recall_supporting_facts
if not getattr(_impl._recall_candidate_interfaces, "_qualibug_authority_wrapper", False):
    _authority_recall_candidate_interfaces._qualibug_authority_wrapper = True
    _impl._recall_candidate_interfaces = _authority_recall_candidate_interfaces


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (value or []) if isinstance(row, dict)]


def _is_rule_interface(row: dict[str, Any]) -> bool:
    return str(row.get("relation") or row.get("relation_type") or "").strip() == "rule_to_interface"


def _is_rule_prompt(prompt: str) -> bool:
    return '"assessment_mode":"rule_to_interface"' in prompt or '"assessment_mode": "rule_to_interface"' in prompt


class _PagingClient:
    """Reuse identical rule responses across transition paging windows."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._rule_responses: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.forwarded_calls = 0
        self.cached_rule_calls = 0

    def complete_json(self, **kwargs: Any) -> dict[str, Any]:
        prompt = str(kwargs.get("user_prompt") or "")
        if _is_rule_prompt(prompt):
            with self._lock:
                cached = self._rule_responses.get(prompt)
            if cached is not None:
                with self._lock:
                    self.cached_rule_calls += 1
                return deepcopy(cached)
        response = self._client.complete_json(**kwargs)
        with self._lock:
            self.forwarded_calls += 1
            if _is_rule_prompt(prompt) and isinstance(response, dict):
                self._rule_responses[prompt] = deepcopy(response)
        return response

    def usage_snapshot(self) -> dict[str, float]:
        snapshot = self._client.usage_snapshot()
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _run_core_with_transition_window(asset: dict[str, Any], *, client: Any, transition_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    token = _TRANSITION_ROWS_OVERRIDE.set(tuple(dict(row) for row in transition_rows))
    try:
        return _impl.enrich_knowledge_asset_with_agent_relationships(asset, client=client)
    finally:
        _TRANSITION_ROWS_OVERRIDE.reset(token)


def _unique_assessments(receipts: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        for row in receipt.get(key, []) or []:
            if not isinstance(row, dict):
                continue
            identity = str(row.get("rule_id") if key == "rule_assessments" else row.get("transition_id") or "")
            if not identity:
                continue
            current = result.get(identity)
            if current is None or (
                str(current.get("disposition") or "") != "LINKED"
                and str(row.get("disposition") or "") == "LINKED"
            ):
                result[identity] = dict(row)
    return list(result.values())


def _merge_candidate_recall(receipts: list[dict[str, Any]], *, duplicate_rule_windows: bool = False) -> dict[str, Any]:
    rows = [r.get("candidate_recall") for r in receipts if isinstance(r.get("candidate_recall"), dict)]
    if not rows:
        return {"rule_count": 0, "candidate_total": 0, "candidate_min": None, "candidate_max": 0, "fallback_rule_count": 0, "empty_candidate_rule_count": 0, "recall_basis": "mixed"}
    bases = {str(r.get("recall_basis") or "") for r in rows if str(r.get("recall_basis") or "")}
    if duplicate_rule_windows:
        first = rows[0]
        return {
            "rule_count": int(first.get("rule_count") or 0),
            "candidate_total": sum(int(r.get("candidate_total") or 0) for r in rows),
            "candidate_min": min([r.get("candidate_min") for r in rows if r.get("candidate_min") is not None], default=None),
            "candidate_max": max([int(r.get("candidate_max") or 0) for r in rows], default=0),
            "fallback_rule_count": sum(int(r.get("fallback_rule_count") or 0) for r in rows),
            "empty_candidate_rule_count": sum(int(r.get("empty_candidate_rule_count") or 0) for r in rows),
            "recall_basis": next(iter(bases)) if len(bases) == 1 else "mixed",
        }
    mins = [r.get("candidate_min") for r in rows if r.get("candidate_min") is not None]
    return {
        "rule_count": sum(int(r.get("rule_count") or 0) for r in rows),
        "candidate_total": sum(int(r.get("candidate_total") or 0) for r in rows),
        "candidate_min": min(mins) if mins else None,
        "candidate_max": max([int(r.get("candidate_max") or 0) for r in rows], default=0),
        "fallback_rule_count": sum(int(r.get("fallback_rule_count") or 0) for r in rows),
        "empty_candidate_rule_count": sum(int(r.get("empty_candidate_rule_count") or 0) for r in rows),
        "recall_basis": next(iter(bases)) if len(bases) == 1 else "mixed",
    }


def _merge_status(receipts: list[dict[str, Any]]) -> str:
    if any(r.get("rejections") for r in receipts):
        return "VERIFIED_WITH_REJECTIONS"
    if any(r.get("failed_units") for r in receipts):
        return "VERIFIED_WITH_FAILED_UNITS"
    rule_assessments = _unique_assessments(receipts, "rule_assessments")
    transition_assessments = _unique_assessments(receipts, "transition_assessments")
    if any(r.get("unassessed_rule_count") or r.get("budget_skipped_rule_count") or r.get("unassessed_transition_count") for r in receipts):
        return "VERIFIED_WITH_GAPS"
    if any(
        row.get("disposition") != "LINKED" or row.get("accepted_relationship_count") == 0
        for row in [*rule_assessments, *transition_assessments]
        if isinstance(row, dict)
    ):
        return "VERIFIED_WITH_GAPS"
    return "VERIFIED"


def _merge_receipts(receipts: list[dict[str, Any]], *, rule_count: int, duplicate_rule_windows: bool = False) -> dict[str, Any]:
    first = receipts[0]
    merged = dict(first)
    rule_assessments = _unique_assessments(receipts, "rule_assessments")
    transition_assessments = _unique_assessments(receipts, "transition_assessments")
    merged["rule_count"] = rule_count
    merged["assessed_rule_count"] = len(rule_assessments)
    merged["unassessed_rule_ids"] = list(dict.fromkeys(x for r in receipts for x in r.get("unassessed_rule_ids", [])))
    merged["unassessed_rule_count"] = len(merged["unassessed_rule_ids"])
    merged["budget_skipped_rule_ids"] = list(dict.fromkeys(x for r in receipts for x in r.get("budget_skipped_rule_ids", [])))
    merged["budget_skipped_rule_count"] = len(merged["budget_skipped_rule_ids"])
    merged["batch_count"] = sum(int(r.get("batch_count") or 0) for r in receipts)
    merged["request_count"] = sum(int(r.get("request_count") or 0) for r in receipts)
    merged["transition_request_count"] = sum(int(r.get("transition_request_count") or 0) for r in receipts)
    merged["context_fact_count"] = max([int(r.get("context_fact_count") or 0) for r in receipts], default=0)
    merged["context_fact_omitted_count"] = sum(int(r.get("context_fact_omitted_count") or 0) for r in receipts)
    merged["supporting_fact_pool_count"] = max([int(r.get("supporting_fact_pool_count") or 0) for r in receipts], default=0)
    merged["candidate_recall"] = _merge_candidate_recall(receipts, duplicate_rule_windows=duplicate_rule_windows)
    for key in (
        "proposal_count", "accepted_relationship_count", "rejected_proposal_count",
        "rejected_low_confidence_count", "rejected_invalid_identity_count", "rejected_non_candidate_count",
        "rejected_invalid_evidence_count", "rejected_duplicate_count", "rejected_rule_limit_count",
        "rejected_inconsistent_disposition_count", "existing_relationship_count", "no_executable_interface_count",
        "ambiguous_rule_count", "provider_attempt_count", "provider_retry_count",
    ):
        merged[key] = sum(int(r.get(key) or 0) for r in receipts)
    merged["transition_count"] = sum(int(r.get("transition_count") or 0) for r in receipts)
    merged["transition_budget_skipped_count"] = sum(int(r.get("transition_budget_skipped_count") or 0) for r in receipts)
    merged["assessed_transition_count"] = len(transition_assessments)
    merged["unassessed_transition_ids"] = list(dict.fromkeys(x for r in receipts for x in r.get("unassessed_transition_ids", [])))
    merged["unassessed_transition_count"] = len(merged["unassessed_transition_ids"])
    merged["no_executable_transition_count"] = sum(x.get("disposition") == "NO_EXECUTABLE_INTERFACE" for x in transition_assessments)
    merged["ambiguous_transition_count"] = sum(x.get("disposition") == "AMBIGUOUS" for x in transition_assessments)
    merged["transition_assessments"] = transition_assessments
    merged["rule_assessments"] = rule_assessments
    merged["rejections"] = [row for r in receipts for row in r.get("rejections", [])]
    merged["failed_units"] = [row for r in receipts for row in r.get("failed_units", [])]
    merged["failed_unit_count"] = len(merged["failed_units"])
    merged["accepted_edge_ids"] = list(dict.fromkeys(x for r in receipts for x in r.get("accepted_edge_ids", []) if x))
    usage: dict[str, float] = {}
    for r in receipts:
        for key, value in (r.get("usage") or {}).items():
            try:
                usage[key] = usage.get(key, 0.0) + float(value)
            except (TypeError, ValueError):
                pass
    merged["usage"] = usage
    merged["status"] = _merge_status(receipts)
    merged["receipt_fingerprint"] = _impl._fingerprint(merged)
    return merged


def _merge_generated_relationships(governed_asset: dict[str, Any], enriched_assets: list[dict[str, Any]], receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relationships = [dict(row) for row in _dicts(governed_asset.get("relationships"))]
    seen_edges = {str(row.get("edge_id")) for row in relationships if row.get("edge_id")}
    for enriched, receipt in zip(enriched_assets, receipts):
        accepted_edge_ids = {str(x).strip() for x in receipt.get("accepted_edge_ids", []) if str(x).strip()}
        for row in _dicts(enriched.get("relationships")):
            edge_id = str(row.get("edge_id") or "").strip()
            if edge_id and edge_id in accepted_edge_ids and edge_id not in seen_edges:
                seen_edges.add(edge_id)
                relationships.append(dict(row))
    return relationships


def _transition_paged_enrichment(governed_asset: dict[str, Any], *, client: Any | None) -> tuple[dict[str, Any], dict[str, Any]]:
    transitions = _dicts(_original_asset_transition_rows(governed_asset))
    if len(transitions) <= _TRANSITION_BATCH_WINDOW:
        return _impl.enrich_knowledge_asset_with_agent_relationships(governed_asset, client=client)
    base_client = client or _impl._default_client()
    paging_client = _PagingClient(base_client)
    chunks = [transitions[i:i + _TRANSITION_BATCH_WINDOW] for i in range(0, len(transitions), _TRANSITION_BATCH_WINDOW)]
    receipts: list[dict[str, Any]] = []
    enriched_assets: list[dict[str, Any]] = []
    for chunk in chunks:
        enriched, receipt = _run_core_with_transition_window(governed_asset, client=paging_client, transition_rows=chunk)
        enriched_assets.append(enriched)
        receipts.append(receipt)
    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = _merge_generated_relationships(governed_asset, enriched_assets, receipts)
    merged_receipt = _merge_receipts(receipts, rule_count=len(_dicts(governed_asset.get("rule_library"))), duplicate_rule_windows=True)
    merged_receipt["accepted_relationship_count"] = len(merged_receipt["accepted_edge_ids"])
    merged_receipt["provider_attempt_count"] = max(0, int(merged_receipt.get("provider_attempt_count") or 0) - paging_client.cached_rule_calls)
    merged_receipt["request_count"] = max(0, int(merged_receipt.get("request_count") or 0) - paging_client.cached_rule_calls)
    merged_receipt["transition_paging"] = {
        "enabled": True,
        "window_size": _TRANSITION_BATCH_WINDOW,
        "window_count": len(chunks),
        "transition_count": len(transitions),
        "budget_skipped_transition_count": 0,
        "rule_response_reuse_count": paging_client.cached_rule_calls,
        "reason_code": "SOURCE_TRANSITIONS_PAGED_INSTEAD_OF_TRUNCATED",
    }
    merged_receipt["transition_budget_skipped_count"] = 0
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


def _candidate_paged_enrichment(governed_asset: dict[str, Any], *, client: Any | None) -> tuple[dict[str, Any], dict[str, Any]]:
    interfaces = _dicts(governed_asset.get("interfaces"))
    if len(interfaces) <= _CANDIDATE_BATCH_WINDOW:
        return _transition_paged_enrichment(governed_asset, client=client)
    chunks = [interfaces[i:i + _CANDIDATE_BATCH_WINDOW] for i in range(0, len(interfaces), _CANDIDATE_BATCH_WINDOW)]
    receipts: list[dict[str, Any]] = []
    enriched_assets: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        chunk_asset = deepcopy(governed_asset)
        chunk_asset["interfaces"] = chunk
        if index > 0:
            chunk_asset["state_machines"] = []
        enriched, receipt = _transition_paged_enrichment(chunk_asset, client=client)
        enriched_assets.append(enriched)
        receipts.append(receipt)
    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = _merge_generated_relationships(governed_asset, enriched_assets, receipts)
    merged_receipt = _merge_receipts(receipts, rule_count=len(_dicts(governed_asset.get("rule_library"))), duplicate_rule_windows=True)
    merged_receipt["accepted_relationship_count"] = len(merged_receipt["accepted_edge_ids"])
    merged_receipt["candidate_window_rule_assessment_count"] = sum(len(receipt.get("rule_assessments", []) or []) for receipt in receipts)
    merged_receipt["candidate_paging"] = {
        "enabled": True,
        "window_size": _CANDIDATE_BATCH_WINDOW,
        "window_count": len(chunks),
        "source_interface_count": len(interfaces),
        "window_interface_counts": [len(chunk) for chunk in chunks],
        "candidate_budget_skipped_count": 0,
        "candidate_window_fill_enabled": True,
        "reason_code": "SOURCE_INTERFACES_PAGED_INSTEAD_OF_TOP_CANDIDATE_TRUNCATION",
    }
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


def _fact_paged_enrichment(governed_asset: dict[str, Any], *, client: Any | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Page supporting facts only for rules that remain unresolved.

    The core linker keeps a bounded per-rule evidence slice. When that slice
    is exhausted without a LINKED assessment, this authority layer exposes the
    next source-backed fact window and re-runs only the unresolved rules.
    Already-linked rules are not repeated, which keeps the Recall repair from
    multiplying provider work for the common resolved path. State transitions
    remain first-window-only, matching the existing candidate/transition
    paging contract.
    """
    fact_pool = _original_all_fact_rows(governed_asset)
    if len(fact_pool) <= _FACT_BATCH_WINDOW:
        if not _linker_budget_available():
            merged = deepcopy(governed_asset)
            receipt = {
                "status": "VERIFIED_WITH_GAPS",
                "reason_code": LINKER_WINDOW_BUDGET_EXHAUSTED,
                "accepted_edge_ids": [],
                "unassessed_rule_ids": [
                    _text(row.get("rule_id"))
                    for row in _dicts(governed_asset.get("rule_library"))
                ],
                "unassessed_rule_count": len(_dicts(governed_asset.get("rule_library"))),
            }
            receipt["receipt_fingerprint"] = _impl._fingerprint(receipt)
            merged["agent_semantic_link_receipt"] = receipt
            return merged, receipt
        _linker_budget_consume()
        return _candidate_paged_enrichment(governed_asset, client=client)

    base_client = client or _impl._default_client()
    chunks = [
        fact_pool[index:index + _FACT_BATCH_WINDOW]
        for index in range(0, len(fact_pool), _FACT_BATCH_WINDOW)
    ]
    remaining_rules = _dicts(governed_asset.get("rule_library"))
    receipts: list[dict[str, Any]] = []
    enriched_assets: list[dict[str, Any]] = []
    consumed_chunks: list[list[dict[str, Any]]] = []
    unresolved_rule_counts: list[int] = []
    budget_stopped = False

    for index, chunk in enumerate(chunks):
        if not remaining_rules:
            break
        if not _linker_budget_available():
            unresolved_rule_counts.append(len(remaining_rules))
            budget_stopped = True
            break
        _linker_budget_consume()
        chunk_asset = deepcopy(governed_asset)
        chunk_asset["rule_library"] = remaining_rules
        if index > 0:
            chunk_asset["state_machines"] = []
        token = _FACT_ROWS_OVERRIDE.set(tuple(dict(row) for row in chunk))
        try:
            enriched, receipt = _candidate_paged_enrichment(chunk_asset, client=base_client)
        finally:
            _FACT_ROWS_OVERRIDE.reset(token)
        receipts.append(receipt)
        enriched_assets.append(enriched)
        consumed_chunks.append([dict(row) for row in chunk])

        assessments = {
            _text(row.get("rule_id")): row
            for row in receipt.get("rule_assessments", []) or []
            if isinstance(row, dict) and _text(row.get("rule_id"))
        }
        next_rules: list[dict[str, Any]] = []
        for rule in remaining_rules:
            rule_id = _text(rule.get("rule_id"))
            assessment = assessments.get(rule_id)
            if (
                assessment is None
                or _text(assessment.get("disposition")).upper() != "LINKED"
                or int(assessment.get("accepted_relationship_count") or 0) <= 0
            ):
                next_rules.append(dict(rule))
        remaining_rules = next_rules
        unresolved_rule_counts.append(len(remaining_rules))

    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = _merge_generated_relationships(governed_asset, enriched_assets, receipts)
    merged_receipt = _merge_receipts(
        receipts,
        rule_count=len(_dicts(governed_asset.get("rule_library"))),
        duplicate_rule_windows=True,
    )
    merged_receipt["accepted_relationship_count"] = len(merged_receipt["accepted_edge_ids"])
    merged_receipt["supporting_fact_pool_count"] = len(fact_pool)
    merged_receipt["context_fact_omitted_count"] = 0
    fact_paging = {
        "enabled": True,
        "window_size": _FACT_BATCH_WINDOW,
        "window_count": len(consumed_chunks),
        "windows_executed": len(consumed_chunks),
        "source_fact_count": len(fact_pool),
        "window_fact_counts": [len(chunk) for chunk in consumed_chunks],
        "unconsumed_tail_fact_count": max(
            0,
            len(fact_pool) - sum(len(chunk) for chunk in consumed_chunks),
        ),
        "fact_budget_skipped_count": 0,
        "unresolved_rule_counts_after_window": unresolved_rule_counts,
        "zero_score_fact_fill_enabled": True,
        "reason_code": "SOURCE_SUPPORTING_FACTS_PAGED_UNTIL_RULE_CLOSURE",
    }
    if budget_stopped:
        fact_paging.update({
            "window_budget_exhausted": True,
            "reason_code": LINKER_WINDOW_BUDGET_EXHAUSTED,
        })
        merged_receipt["unassessed_rule_ids"] = sorted({
            _text(row.get("rule_id")) for row in remaining_rules
        } | set(merged_receipt.get("unassessed_rule_ids") or []))
        merged_receipt["unassessed_rule_count"] = len(merged_receipt["unassessed_rule_ids"])
    merged_receipt["supporting_fact_paging"] = fact_paging
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


def _lossless_rule_enrichment(governed_asset: dict[str, Any], *, client: Any | None) -> tuple[dict[str, Any], dict[str, Any]]:
    rules = _dicts(governed_asset.get("rule_library"))
    if len(rules) <= _RULE_BATCH_WINDOW:
        return _fact_paged_enrichment(governed_asset, client=client)
    chunks = [rules[i:i + _RULE_BATCH_WINDOW] for i in range(0, len(rules), _RULE_BATCH_WINDOW)]
    receipts: list[dict[str, Any]] = []
    generated_relationships: list[dict[str, Any]] = []
    budget_exhausted = False
    skipped_rule_count = 0
    for index, chunk in enumerate(chunks):
        if not _linker_budget_available():
            # Declared ceiling reached: remaining rule windows are skipped with
            # a named reason and counted — never a silent truncation, never an
            # unbounded provider spend (measured 5M-token burn, CMP_77d5dfe1).
            budget_exhausted = True
            skipped_rule_count += len(chunk)
            continue
        chunk_asset = deepcopy(governed_asset)
        chunk_asset["rule_library"] = chunk
        if index > 0:
            chunk_asset["state_machines"] = []
        enriched, receipt = _fact_paged_enrichment(chunk_asset, client=client)
        receipts.append(receipt)
        accepted_edge_ids = {str(x).strip() for x in receipt.get("accepted_edge_ids", []) if str(x).strip()}
        generated_relationships.extend(dict(row) for row in _dicts(enriched.get("relationships")) if str(row.get("edge_id") or "").strip() in accepted_edge_ids)
    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = [*(_dicts(governed_asset.get("relationships"))), *generated_relationships]
    merged_receipt = _merge_receipts(receipts, rule_count=len(rules), duplicate_rule_windows=False)
    if budget_exhausted:
        merged_receipt["unassessed_rule_count"] = int(
            merged_receipt.get("unassessed_rule_count") or 0
        ) + skipped_rule_count
        merged_receipt.setdefault("unassessed_rule_ids", [])
    scheduling = {
        "enabled": True,
        "window_size": _RULE_BATCH_WINDOW,
        "window_count": len(chunks),
        "windows_executed": (
            _linker_budget_used()
            if _LINKER_WINDOW_BUDGET.get() is not None
            else len(receipts)
        ),
        "window_budget": _linker_max_windows(),
        "budget_skipped_rule_count": merged_receipt["budget_skipped_rule_count"],
    }
    if budget_exhausted:
        scheduling.update({
            "window_budget_exhausted": True,
            "window_budget_skipped_rule_count": skipped_rule_count,
            "reason_code": LINKER_WINDOW_BUDGET_EXHAUSTED,
        })
    else:
        scheduling["reason_code"] = "SOURCE_RULES_PAGED_INSTEAD_OF_GLOBALLY_TRUNCATED"
    merged_receipt["lossless_rule_scheduling"] = scheduling
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


def enrich_knowledge_asset_with_agent_relationships(knowledge_asset: dict[str, Any], *, client: Any | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(knowledge_asset, dict):
        raise AgentSemanticLinkerError("knowledge_asset_not_object")
    # ── Provider-CALL ceiling (the real spend unit) ─────────────────────────
    # The window budget below counts paging windows, but ONE window's cascade
    # (candidate + relationship follow-ups + confidence/omitted recoveries)
    # can fire dozens of provider calls — measured 2026-08-23: two rounds
    # burned the entire 5M-token run budget through windows that each looked
    # harmless. Calls are the atomic spend unit, so cap THEM at the shared
    # chat_json boundary via the caller id this linker already declares.
    from .llm_reasoning import clear_caller_call_budget, install_caller_call_budget

    raw_calls = str(os.environ.get("QUALIBUG_AGENT_LINKER_MAX_PROVIDER_CALLS") or "").strip()
    try:
        max_calls = int(raw_calls) if raw_calls else 40
    except ValueError:
        max_calls = 40
    install_caller_call_budget("agent_semantic_linker", max_calls)
    try:
        return _enrich_with_budgeted_windows(knowledge_asset, client=client)
    finally:
        clear_caller_call_budget("agent_semantic_linker")


def _enrich_with_budgeted_windows(knowledge_asset: dict[str, Any], *, client: Any | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(knowledge_asset, dict):
        raise AgentSemanticLinkerError("knowledge_asset_not_object")
    original_relationships = _dicts(knowledge_asset.get("relationships"))
    governed_existing: list[dict[str, Any]] = []
    ungoverned_existing: list[dict[str, Any]] = []
    for row in original_relationships:
        if not _is_rule_interface(row) or _relationship_is_authoritative(row):
            governed_existing.append(dict(row))
        else:
            ungoverned_existing.append(dict(row))
    governed_asset = deepcopy(knowledge_asset)
    governed_asset["relationships"] = governed_existing
    _linker_budget_install(_linker_max_windows())
    try:
        enriched, receipt = _lossless_rule_enrichment(governed_asset, client=client)
    finally:
        _LINKER_WINDOW_BUDGET.set(None)
    accepted_edge_ids = {str(x).strip() for x in receipt.get("accepted_edge_ids", []) if str(x).strip()}
    generated = [dict(row) for row in _dicts(enriched.get("relationships")) if str(row.get("edge_id") or "").strip() in accepted_edge_ids]
    preserved = [dict(row) for row in original_relationships if str(row.get("edge_id") or "").strip() not in accepted_edge_ids]
    enriched["relationships"] = [*preserved, *generated]
    receipt.update({
        "ungoverned_existing_relationship_count": len(ungoverned_existing),
        "ungoverned_existing_relationships_suppressed_from_dedupe": True,
        "existing_relationship_authority_reused": True,
        "parallel_semantic_linker_created": False,
    })
    receipt["receipt_fingerprint"] = _impl._fingerprint(receipt)
    enriched["agent_semantic_link_receipt"] = receipt
    return enriched, receipt


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


__all__ = ["RECEIPT_SCHEMA", "PROMPT_PROTOCOL", "AgentSemanticLinkerError", "enrich_knowledge_asset_with_agent_relationships"]
