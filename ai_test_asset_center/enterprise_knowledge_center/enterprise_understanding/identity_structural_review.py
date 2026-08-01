"""Operator review closure for structural enterprise-identity candidates.

The existing operator authority ledger remains the only durable decision store.
Structural candidates are product output, so they never enter blind Ground Truth.
A confirmed candidate is revalidated against the current structural fingerprint,
then projected as one operator-authorized TERM_ALIAS fact and rebuilt through the
canonical identity graph. Rejected or stale candidates never change identity.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

from .._chinese_business_authority_decision import (
    AUDIT_SCHEMA,
    DECISION_SCHEMA,
    LEDGER_SCHEMA,
    load_authority_decision_ledger,
    save_authority_decision_ledger,
)
from .._common import ROOT, _safe_project_id
from .._utils import _now
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

REVIEW_QUEUE_SCHEMA = "qualibug.enterprise-identity-structural-review-queue.v1"
REVIEW_RECEIPT_SCHEMA = "qualibug.enterprise-identity-structural-review-receipt.v1"
DECISION_KIND = "IDENTITY_STRUCTURAL_CANDIDATE"
ACTION_CONFIRM_ALIAS = "CONFIRM_IDENTITY_ALIAS"
ACTION_REJECT_CANDIDATE = "REJECT_IDENTITY_CANDIDATE"
_ALLOWED_ACTIONS = {ACTION_CONFIRM_ALIAS, ACTION_REJECT_CANDIDATE}
_SYNTHETIC_SOURCE_ID = "operator-authority"
_REBUILD_FLAG = "_identity_structural_review_rebuild_in_progress"
_PENDING_RECEIPT = "_identity_structural_review_pending_receipt"


def _actor_identity(actor: Any) -> dict[str, str]:
    row = as_dict(actor)
    name = text(
        row.get("name")
        or row.get("username")
        or row.get("actor_id")
        or row.get("id")
    )
    if not name:
        raise ValueError("identity_structural_review_actor_required")
    return {
        "name": name,
        "role": text(row.get("role")),
        "tenant_id": text(row.get("tenant_id") or row.get("tenant")),
    }


def _evidence_identity(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        text(row.get("source_id")),
        text(row.get("source_locator") or row.get("locator")),
        text(row.get("quote_hash")),
        text(row.get("fact_id")),
        text(row.get("asset_ref")),
        text(row.get("derivation")),
    )


def identity_structural_candidate_fingerprint(candidate: dict[str, Any]) -> str:
    material = {
        "candidate_entity_ids": sorted(
            unique_text(as_list(candidate.get("candidate_entity_ids")))
        ),
        "canonical_labels": dict(
            sorted(as_dict(candidate.get("canonical_labels")).items())
        ),
        "matched_dimensions": sorted(
            unique_text(as_list(candidate.get("matched_dimensions")))
        ),
        "matched_operation_names": sorted(
            unique_text(as_list(candidate.get("matched_operation_names")))
        ),
        "matched_lifecycle_states": sorted(
            unique_text(as_list(candidate.get("matched_lifecycle_states")))
        ),
        "matched_lifecycle_transitions": sorted(
            unique_text(as_list(candidate.get("matched_lifecycle_transitions")))
        ),
        "matched_relation_context": sorted(
            unique_text(as_list(candidate.get("matched_relation_context")))
        ),
        "source_refs": {
            key: sorted(unique_text(as_list(value)))
            for key, value in sorted(as_dict(candidate.get("source_refs")).items())
        },
        "evidence": sorted(
            _evidence_identity(row)
            for row in as_list(candidate.get("evidence"))
            if isinstance(row, dict)
        ),
    }
    return stable_id("enterprise_identity_structural_candidate_fingerprint", material)


def _candidate_rows(asset: dict[str, Any], model: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in as_list(model.get("identity_structural_candidates"))
        if isinstance(row, dict) and text(row.get("candidate_id"))
    ]
    if rows:
        return rows
    receipt = as_dict(
        model.get("identity_structural_evidence")
        or asset.get("enterprise_identity_structural_evidence")
    )
    return [
        dict(row)
        for row in as_list(receipt.get("candidate_pairs"))
        if isinstance(row, dict) and text(row.get("candidate_id"))
    ]


def _structural_decisions(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in as_list(asset.get("identity_structural_review_decisions"))
        if isinstance(row, dict)
        and text(row.get("schema")) == DECISION_SCHEMA
        and text(row.get("decision_kind")) == DECISION_KIND
    ]


def _latest_decisions(decisions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in decisions:
        candidate_id = text(row.get("candidate_id") or row.get("conflict_id"))
        if candidate_id:
            latest[candidate_id] = row
    return latest


def _decision_matches(
    decision: dict[str, Any], candidate: dict[str, Any]
) -> tuple[bool, str]:
    entity_ids = sorted(unique_text(as_list(candidate.get("candidate_entity_ids"))))
    recorded_ids = sorted(
        unique_text(
            as_list(
                decision.get("participant_entity_ids")
                or decision.get("candidate_entity_ids")
            )
        )
    )
    if recorded_ids != entity_ids:
        return False, "STRUCTURAL_CANDIDATE_PARTICIPANT_DRIFT"
    current_fingerprint = identity_structural_candidate_fingerprint(candidate)
    if text(decision.get("candidate_fingerprint")) != current_fingerprint:
        return False, "STRUCTURAL_CANDIDATE_FINGERPRINT_DRIFT"
    return True, ""


def _decision_status(
    decision: dict[str, Any] | None, candidate: dict[str, Any]
) -> tuple[str, str]:
    if not decision:
        return "PENDING_REVIEW", ""
    matches, reason = _decision_matches(decision, candidate)
    if not matches:
        return "STALE_DECISION", reason
    action = text(decision.get("action"))
    if action == ACTION_CONFIRM_ALIAS:
        return "CONFIRMED", ""
    if action == ACTION_REJECT_CANDIDATE:
        return "REJECTED", ""
    return "INVALID_DECISION", "STRUCTURAL_CANDIDATE_ACTION_INVALID"


def project_identity_structural_review_queue(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    candidates = _candidate_rows(asset, model)
    latest = _latest_decisions(_structural_decisions(asset))
    tasks: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = text(candidate.get("candidate_id"))
        decision = latest.get(candidate_id)
        status, stale_reason = _decision_status(decision, candidate)
        fingerprint = identity_structural_candidate_fingerprint(candidate)
        tasks.append(
            {
                "review_task_id": stable_id(
                    "enterprise_identity_structural_review_task",
                    candidate_id,
                    fingerprint,
                ),
                "candidate_id": candidate_id,
                "candidate_fingerprint": fingerprint,
                "candidate_entity_ids": sorted(
                    unique_text(as_list(candidate.get("candidate_entity_ids")))
                ),
                "canonical_labels": deepcopy(
                    as_dict(candidate.get("canonical_labels"))
                ),
                "strength": candidate.get("strength"),
                "matched_dimensions": list(
                    as_list(candidate.get("matched_dimensions"))
                ),
                "matched_operation_names": list(
                    as_list(candidate.get("matched_operation_names"))
                ),
                "matched_lifecycle_states": list(
                    as_list(candidate.get("matched_lifecycle_states"))
                ),
                "matched_lifecycle_transitions": list(
                    as_list(candidate.get("matched_lifecycle_transitions"))
                ),
                "matched_relation_context": list(
                    as_list(candidate.get("matched_relation_context"))
                ),
                "source_refs": deepcopy(as_dict(candidate.get("source_refs"))),
                "evidence": dedupe_evidence(
                    row
                    for row in as_list(candidate.get("evidence"))
                    if isinstance(row, dict)
                ),
                "review_status": status,
                "stale_reason_code": stale_reason,
                "decision_id": text(as_dict(decision).get("decision_id")),
                "decision_action": text(as_dict(decision).get("action")),
                "requires_explicit_canonical_entity_selection": True,
                "automatic_resolution_allowed": False,
                "automatic_entity_union_allowed": False,
            }
        )
    tasks.sort(
        key=lambda row: (text(row.get("review_status")), text(row.get("candidate_id")))
    )
    queue = {
        "schema": REVIEW_QUEUE_SCHEMA,
        "queue_id": stable_id(
            "enterprise_identity_structural_review_queue",
            [(row["candidate_id"], row["candidate_fingerprint"]) for row in tasks],
        ),
        "task_count": len(tasks),
        "pending_count": sum(
            1 for row in tasks if text(row.get("review_status")) == "PENDING_REVIEW"
        ),
        "confirmed_count": sum(
            1 for row in tasks if text(row.get("review_status")) == "CONFIRMED"
        ),
        "rejected_count": sum(
            1 for row in tasks if text(row.get("review_status")) == "REJECTED"
        ),
        "stale_decision_count": sum(
            1 for row in tasks if text(row.get("review_status")) == "STALE_DECISION"
        ),
        "tasks": tasks,
        "uses_existing_operator_authority_ledger": True,
        "blind_ground_truth_workflow_used": False,
        "product_candidates_enter_ground_truth": False,
        "automatic_resolution_allowed": False,
    }
    model["identity_structural_review_queue"] = queue
    asset["enterprise_identity_structural_review_queue"] = deepcopy(queue)
    return queue


def _operator_alias_fact(
    decision: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    entity_ids = sorted(unique_text(as_list(candidate.get("candidate_entity_ids"))))
    canonical_entity_id = text(decision.get("canonical_entity_id"))
    if canonical_entity_id not in entity_ids or len(entity_ids) != 2:
        raise ValueError("identity_structural_review_canonical_entity_invalid")
    alias_entity_id = next(value for value in entity_ids if value != canonical_entity_id)
    labels = as_dict(candidate.get("canonical_labels"))
    canonical_label = text(labels.get(canonical_entity_id))
    alias_label = text(labels.get(alias_entity_id))
    if not canonical_label or not alias_label or canonical_label == alias_label:
        raise ValueError("identity_structural_review_labels_invalid")
    decision_id = text(decision.get("decision_id"))
    statement = (
        f'操作员确认“{alias_label}”又称“{canonical_label}”，'
        "两者为同一业务对象。"
    )
    quote_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    fact_id = stable_id(
        "operator_confirmed_identity_term_alias",
        decision_id,
        canonical_entity_id,
        alias_entity_id,
        canonical_label,
        alias_label,
    )
    return {
        "fact_id": fact_id,
        "kind": "TERM_ALIAS",
        "status": "ACCEPTED",
        "canonical_term": canonical_label,
        "alias": alias_label,
        "raw_statement": statement,
        "source_id": _SYNTHETIC_SOURCE_ID,
        "source_locator": f"operator_authority_decisions.json#{decision_id}",
        "source_spans": [
            {
                "source_id": _SYNTHETIC_SOURCE_ID,
                "source_locator": f"operator_authority_decisions.json#{decision_id}",
                "locator": f"operator_authority_decisions.json#{decision_id}",
                "quote": statement,
                "quote_hash": quote_hash,
            }
        ],
        "identity_evidence_class": "EXPLICIT_ALIAS",
        "formal_identity_union_allowed": True,
        "formal_promotion_allowed": True,
        "generated_from_structural_identity_review": True,
        "operator_authority_decision_id": decision_id,
        "structural_candidate_id": candidate.get("candidate_id"),
        "structural_candidate_fingerprint": identity_structural_candidate_fingerprint(
            candidate
        ),
        "canonical_entity_id": canonical_entity_id,
        "alias_entity_id": alias_entity_id,
        "supporting_structural_evidence": dedupe_evidence(
            row
            for row in as_list(candidate.get("evidence"))
            if isinstance(row, dict)
        ),
        "automatic_resolution_allowed": False,
    }


def _append_alias_fact(asset: dict[str, Any], fact: dict[str, Any]) -> bool:
    ledger = dict(as_dict(asset.get("business_fact_ledger")))
    items = [
        dict(row)
        for row in as_list(ledger.get("items"))
        if isinstance(row, dict)
    ]
    fact_id = text(fact.get("fact_id"))
    if any(text(row.get("fact_id")) == fact_id for row in items):
        return False
    items.append(fact)
    ledger["items"] = items
    asset["business_fact_ledger"] = ledger
    return True


def _authorize_registry_merge(
    asset: dict[str, Any],
    resolution: dict[str, Any],
    *,
    canonical_entity_id: str,
    alias_entity_id: str,
    decision_id: str,
) -> None:
    registry = dict(
        as_dict(asset.get("enterprise_identity_registry") or resolution.get("registry"))
    )
    entities = [
        dict(row)
        for row in as_list(registry.get("entities"))
        if isinstance(row, dict)
    ]
    registry["entities"] = [
        row for row in entities if text(row.get("entity_id")) != alias_entity_id
    ]
    registry["operator_authorized_merge"] = {
        "decision_id": decision_id,
        "canonical_entity_id": canonical_entity_id,
        "retired_entity_ids": [alias_entity_id],
        "automatic_merge": False,
    }
    asset["enterprise_identity_registry"] = registry


def _benchmark_snapshot(asset: dict[str, Any]) -> dict[str, Any]:
    benchmark = as_dict(asset.get("enterprise_identity_benchmark"))
    return {
        "status": text(benchmark.get("status")),
        "benchmark_id": text(benchmark.get("benchmark_id")),
        "metrics": deepcopy(as_dict(benchmark.get("metrics"))),
        "reason_code": text(benchmark.get("reason_code")),
    }


def apply_identity_structural_review_decisions(
    asset: dict[str, Any],
    model: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    queue = project_identity_structural_review_queue(asset, model)
    latest = _latest_decisions(_structural_decisions(asset))
    candidates = {
        text(row.get("candidate_id")): row for row in _candidate_rows(asset, model)
    }
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    appended = 0
    for candidate_id, decision in latest.items():
        candidate = as_dict(candidates.get(candidate_id))
        if not candidate:
            stale.append(
                {
                    "decision_id": decision.get("decision_id"),
                    "candidate_id": candidate_id,
                    "reason_code": "STRUCTURAL_CANDIDATE_NOT_CURRENT",
                }
            )
            continue
        matches, reason = _decision_matches(decision, candidate)
        if not matches:
            stale.append(
                {
                    "decision_id": decision.get("decision_id"),
                    "candidate_id": candidate_id,
                    "reason_code": reason,
                }
            )
            continue
        action = text(decision.get("action"))
        if action == ACTION_REJECT_CANDIDATE:
            rejected.append(
                {
                    "decision_id": decision.get("decision_id"),
                    "candidate_id": candidate_id,
                    "status": "REJECTED",
                }
            )
            continue
        if action != ACTION_CONFIRM_ALIAS:
            stale.append(
                {
                    "decision_id": decision.get("decision_id"),
                    "candidate_id": candidate_id,
                    "reason_code": "STRUCTURAL_CANDIDATE_ACTION_INVALID",
                }
            )
            continue
        fact = _operator_alias_fact(decision, candidate)
        if _append_alias_fact(asset, fact):
            appended += 1
        canonical_entity_id = text(decision.get("canonical_entity_id"))
        alias_entity_id = text(fact.get("alias_entity_id"))
        _authorize_registry_merge(
            asset,
            resolution,
            canonical_entity_id=canonical_entity_id,
            alias_entity_id=alias_entity_id,
            decision_id=text(decision.get("decision_id")),
        )
        applied.append(
            {
                "decision_id": decision.get("decision_id"),
                "candidate_id": candidate_id,
                "canonical_entity_id": canonical_entity_id,
                "retired_entity_id": alias_entity_id,
                "term_alias_fact_id": fact.get("fact_id"),
                "status": "APPLIED_PENDING_REBUILD",
            }
        )

    receipt = {
        "schema": REVIEW_RECEIPT_SCHEMA,
        "receipt_id": stable_id(
            "enterprise_identity_structural_review_receipt",
            [row.get("decision_id") for row in applied],
            [row.get("decision_id") for row in rejected],
            [row.get("decision_id") for row in stale],
            queue.get("queue_id"),
        ),
        "status": "REBUILD_REQUIRED" if applied else "REVIEW_PROJECTED",
        "review_queue": queue,
        "applied_confirmations": applied,
        "rejected_candidates": rejected,
        "stale_decisions": stale,
        "applied_confirmation_count": len(applied),
        "rejected_count": len(rejected),
        "stale_decision_count": len(stale),
        "alias_fact_appended_count": appended,
        "rebuild_required": bool(applied),
        "measurement_before": _benchmark_snapshot(asset),
        "measurement_after": {},
        "measurement_delta": {},
        "measurement_status": "PENDING_REBUILD" if applied else "NOT_REQUIRED",
        "uses_existing_operator_authority_ledger": True,
        "ground_truth_mutated": False,
        "automatic_entity_union_allowed": False,
    }
    asset["enterprise_identity_structural_review_receipt"] = deepcopy(receipt)
    model["identity_structural_review_receipt"] = deepcopy(receipt)
    return model


def _synthetic_mention_ids(result: dict[str, Any]) -> set[str]:
    return {
        text(row.get("mention_id"))
        for row in as_list(result.get("mentions"))
        if isinstance(row, dict)
        and text(row.get("source_id")) == _SYNTHETIC_SOURCE_ID
        and text(row.get("source_kind")) == "TERM_ALIAS"
        and text(row.get("mention_id"))
    }


def scrub_operator_structural_review_mentions(
    asset: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Keep operator alias authority out of the external source-occurrence universe."""
    synthetic = _synthetic_mention_ids(result)
    if not synthetic:
        return result
    alias_facts = [
        row
        for row in as_list(as_dict(asset.get("business_fact_ledger")).get("items"))
        if isinstance(row, dict)
        and bool(row.get("generated_from_structural_identity_review"))
    ]
    decision_refs = unique_text(
        row.get("operator_authority_decision_id") for row in alias_facts
    )
    evidence = dedupe_evidence(
        span
        for fact in alias_facts
        for span in as_list(fact.get("source_spans"))
        if isinstance(span, dict)
    )
    mentions = [
        row
        for row in as_list(result.get("mentions"))
        if isinstance(row, dict) and text(row.get("mention_id")) not in synthetic
    ]
    retained_edges = [
        row
        for row in as_list(result.get("edges"))
        if isinstance(row, dict)
        and text(row.get("left_mention_id")) not in synthetic
        and text(row.get("right_mention_id")) not in synthetic
    ]
    clusters: list[dict[str, Any]] = []
    for raw in as_list(result.get("clusters")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        original_members = unique_text(as_list(row.get("member_mention_ids")))
        removed = sorted(set(original_members) & synthetic)
        row["member_mention_ids"] = [
            value for value in original_members if value not in synthetic
        ]
        if removed:
            row["operator_identity_merge_decision_refs"] = decision_refs
            row["operator_identity_merge_evidence"] = evidence
            row["operator_identity_merge_authorized"] = True
            row["automatic_identity_merge"] = False
        if row["member_mention_ids"]:
            clusters.append(row)
    result["mentions"] = mentions
    result["edges"] = retained_edges
    result["clusters"] = clusters
    result["mention_to_entity"] = {
        key: value
        for key, value in as_dict(result.get("mention_to_entity")).items()
        if key not in synthetic
    }
    gate = dict(as_dict(result.get("gate")))
    metrics = dict(as_dict(gate.get("metrics")))
    metrics["mention_count"] = len(mentions)
    metrics["identity_edge_count"] = len(retained_edges)
    metrics["operator_review_synthetic_mention_count"] = len(synthetic)
    gate["metrics"] = metrics
    result["gate"] = gate
    result["operator_structural_review_projection"] = {
        "synthetic_mentions_removed_from_benchmark_universe": len(synthetic),
        "operator_decision_refs": decision_refs,
        "ground_truth_universe_changed": False,
    }
    asset["enterprise_identity_resolution"] = result
    asset["enterprise_identity_gate"] = gate
    return result


def finalize_identity_structural_review_measurement(
    asset: dict[str, Any],
    model: dict[str, Any],
    pending_receipt: dict[str, Any],
) -> dict[str, Any]:
    receipt = deepcopy(pending_receipt)
    after = _benchmark_snapshot(asset)
    before = as_dict(receipt.get("measurement_before"))
    before_metrics = as_dict(before.get("metrics"))
    after_metrics = as_dict(after.get("metrics"))
    delta: dict[str, float] = {}
    if text(before.get("status")) == "MEASURED" and text(after.get("status")) == "MEASURED":
        for key in sorted(set(before_metrics) & set(after_metrics)):
            left, right = before_metrics.get(key), after_metrics.get(key)
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                delta[key] = round(float(right) - float(left), 6)
        measurement_status = "MEASURED"
    else:
        measurement_status = "NOT_COMPARABLE"
    receipt.update(
        {
            "status": "APPLIED",
            "rebuild_required": False,
            "measurement_after": after,
            "measurement_delta": delta,
            "measurement_status": measurement_status,
            "ground_truth_mutated": False,
        }
    )
    asset["enterprise_identity_structural_review_receipt"] = deepcopy(receipt)
    model["identity_structural_review_receipt"] = deepcopy(receipt)
    metrics = dict(as_dict(model.get("metrics")))
    metrics.update(
        {
            "enterprise_identity_structural_review_applied_count": int(
                receipt.get("applied_confirmation_count") or 0
            ),
            "enterprise_identity_structural_review_rejected_count": int(
                receipt.get("rejected_count") or 0
            ),
            "enterprise_identity_structural_review_stale_count": int(
                receipt.get("stale_decision_count") or 0
            ),
            "enterprise_identity_structural_review_measurement_comparable": (
                measurement_status == "MEASURED"
            ),
        }
    )
    model["metrics"] = metrics
    return model


def identity_structural_review_rebuild_in_progress(asset: dict[str, Any]) -> bool:
    return bool(asset.get(_REBUILD_FLAG))


def begin_identity_structural_review_rebuild(
    asset: dict[str, Any], receipt: dict[str, Any]
) -> None:
    asset[_REBUILD_FLAG] = True
    asset[_PENDING_RECEIPT] = deepcopy(receipt)


def consume_identity_structural_review_pending_receipt(
    asset: dict[str, Any],
) -> dict[str, Any]:
    asset.pop(_REBUILD_FLAG, None)
    return deepcopy(as_dict(asset.pop(_PENDING_RECEIPT, {})))


def _find_current_candidate(asset: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    model = as_dict(asset.get("enterprise_understanding_model"))
    for row in _candidate_rows(asset, model):
        if text(row.get("candidate_id")) == candidate_id:
            return row
    raise KeyError("identity_structural_review_candidate_not_found")


def get_identity_structural_review_queue(
    project_id: str,
    root: Path | None = None,
    *,
    rebuild_if_missing: bool = True,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    from ..composition import (
        build_enterprise_business_knowledge_asset,
        load_enterprise_business_knowledge_asset,
    )

    asset = load_enterprise_business_knowledge_asset(project, resolved_root)
    if not isinstance(asset, dict) and rebuild_if_missing:
        asset = build_enterprise_business_knowledge_asset(project, resolved_root)
    if not isinstance(asset, dict):
        raise KeyError("identity_structural_review_asset_missing")
    ledger = load_authority_decision_ledger(project, resolved_root)
    asset["identity_structural_review_decisions"] = [
        dict(row)
        for row in as_list(ledger.get("decisions"))
        if isinstance(row, dict)
        and text(row.get("decision_kind")) == DECISION_KIND
    ]
    model = as_dict(asset.get("enterprise_understanding_model"))
    return project_identity_structural_review_queue(asset, model)


def record_identity_structural_review_decision(
    project_id: str,
    *,
    candidate_id: str,
    action: str,
    actor: Any,
    root: Path | None = None,
    canonical_entity_id: str = "",
    rationale: str = "",
    rebuild: bool = True,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    action_code = text(action).upper()
    if action_code not in _ALLOWED_ACTIONS:
        raise ValueError("identity_structural_review_action_invalid")
    actor_row = _actor_identity(actor)
    from ..composition import (
        build_enterprise_business_knowledge_asset,
        load_enterprise_business_knowledge_asset,
    )

    asset = load_enterprise_business_knowledge_asset(project, resolved_root)
    if not isinstance(asset, dict):
        asset = build_enterprise_business_knowledge_asset(project, resolved_root)
    candidate = _find_current_candidate(asset, text(candidate_id))
    entity_ids = sorted(unique_text(as_list(candidate.get("candidate_entity_ids"))))
    if len(entity_ids) != 2:
        raise ValueError("identity_structural_review_requires_pair_candidate")
    canonical = text(canonical_entity_id)
    if action_code == ACTION_CONFIRM_ALIAS and canonical not in entity_ids:
        raise ValueError("identity_structural_review_canonical_entity_required")
    if action_code == ACTION_REJECT_CANDIDATE and canonical:
        raise ValueError("identity_structural_review_reject_forbids_canonical_entity")
    fingerprint = identity_structural_candidate_fingerprint(candidate)
    decided_at = _now()
    decision_id = stable_id(
        "operator_authority_decision",
        project,
        candidate_id,
        action_code,
        canonical,
        actor_row,
        decided_at,
    )
    audit_receipt_id = stable_id(
        "operator_authority_decision_audit", decision_id, decided_at
    )
    alias_entity_id = (
        next(value for value in entity_ids if value != canonical) if canonical else ""
    )
    labels = as_dict(candidate.get("canonical_labels"))
    decision = {
        "schema": DECISION_SCHEMA,
        "decision_kind": DECISION_KIND,
        "decision_id": decision_id,
        "conflict_id": text(candidate_id),
        "candidate_id": text(candidate_id),
        "candidate_fingerprint": fingerprint,
        "participant_entity_ids": entity_ids,
        "participant_fingerprint": stable_id(
            "identity_structural_review_participants", entity_ids
        ),
        "action": action_code,
        "status": "CONFIRMED" if action_code == ACTION_CONFIRM_ALIAS else "REJECTED",
        "canonical_entity_id": canonical,
        "alias_entity_id": alias_entity_id,
        "canonical_label": text(labels.get(canonical)) if canonical else "",
        "alias_label": text(labels.get(alias_entity_id)) if alias_entity_id else "",
        "matched_dimensions": list(as_list(candidate.get("matched_dimensions"))),
        "actor": actor_row,
        "decided_at_utc": decided_at,
        "rationale": text(rationale)[:2000],
        "audit_receipt_id": audit_receipt_id,
        "automatic_resolution_allowed": False,
        "automatic_entity_union_allowed": False,
    }
    audit = {
        "schema": AUDIT_SCHEMA,
        "audit_receipt_id": audit_receipt_id,
        "decision_id": decision_id,
        "decision_kind": DECISION_KIND,
        "candidate_id": text(candidate_id),
        "action": action_code,
        "canonical_entity_id": canonical,
        "participant_entity_ids": entity_ids,
        "actor": actor_row,
        "decided_at_utc": decided_at,
        "rationale": text(rationale)[:2000],
        "project_id": project,
    }
    ledger = load_authority_decision_ledger(project, resolved_root)
    ledger["schema"] = LEDGER_SCHEMA
    ledger["decisions"] = [
        *[
            dict(row)
            for row in as_list(ledger.get("decisions"))
            if isinstance(row, dict)
        ],
        decision,
    ]
    ledger["audit_receipts"] = [
        *[
            dict(row)
            for row in as_list(ledger.get("audit_receipts"))
            if isinstance(row, dict)
        ],
        audit,
    ]
    save_authority_decision_ledger(ledger, project, resolved_root)
    refreshed = (
        build_enterprise_business_knowledge_asset(project, resolved_root)
        if rebuild
        else asset
    )
    return {
        "ok": True,
        "schema": DECISION_SCHEMA,
        "decision": decision,
        "audit_receipt": audit,
        "review_queue": as_dict(
            refreshed.get("enterprise_identity_structural_review_queue")
        ),
        "review_receipt": as_dict(
            refreshed.get("enterprise_identity_structural_review_receipt")
        ),
        "benchmark": as_dict(refreshed.get("enterprise_identity_benchmark")),
        "ground_truth_mutated": False,
    }


__all__ = [
    "ACTION_CONFIRM_ALIAS",
    "ACTION_REJECT_CANDIDATE",
    "DECISION_KIND",
    "REVIEW_QUEUE_SCHEMA",
    "REVIEW_RECEIPT_SCHEMA",
    "apply_identity_structural_review_decisions",
    "begin_identity_structural_review_rebuild",
    "consume_identity_structural_review_pending_receipt",
    "finalize_identity_structural_review_measurement",
    "get_identity_structural_review_queue",
    "identity_structural_candidate_fingerprint",
    "identity_structural_review_rebuild_in_progress",
    "project_identity_structural_review_queue",
    "record_identity_structural_review_decision",
    "scrub_operator_structural_review_mentions",
]
