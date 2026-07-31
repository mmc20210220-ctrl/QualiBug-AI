"""Governance closure around the existing implicit-rule projection authority.

The underlying detector/validator/promoter remains
``implicit_rule_projection.enrich_asset_with_implicit_rule_projection``. This module
adds lifecycle and authority-decision governance after that single projection. Typed
semantic upgrades are reconciled into the existing source-rule identity before the
lifecycle ledger is evaluated, so every public governance entry point sees the same
one-rule authority as the Enterprise Understanding integration boundary.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .implicit_rule_authority_decisions import (
    apply_implicit_rule_authority_decisions,
)
from .implicit_rule_identity_reconciliation import (
    reconcile_implicit_rule_identities,
)
from .implicit_rule_lifecycle import (
    annotate_rule_candidates_with_source_versions,
    project_implicit_rule_lifecycle,
)
from .implicit_rule_projection import enrich_asset_with_implicit_rule_projection

SCHEMA_VERSION = "qualibug.implicit-rule-governance.v1"
_DERIVATION = "implicit_rule_entailment"
_GOVERNANCE_GAP_KINDS = frozenset(
    {
        "IMPLICIT_RULE_STALE",
        "IMPLICIT_RULE_AUTHORITY_DECISION_PENDING",
        "IMPLICIT_RULE_AUTHORITY_DECISION_REJECTED",
        "IMPLICIT_RULE_AUTHORITY_DECISION_CONFLICT",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(_canonical(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _derived_rules(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if isinstance(row, dict) and _text(row.get("derivation")) == _DERIVATION
    ]


def _explicit_rules(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if isinstance(row, dict) and _text(row.get("derivation")) != _DERIVATION
    ]


def _annotate_promoted_rules(
    rules: list[dict[str, Any]], asset: dict[str, Any]
) -> list[dict[str, Any]]:
    annotated = annotate_rule_candidates_with_source_versions(rules, asset)
    for rule in annotated:
        semantic = dict(_dict(rule.get("semantic_contract")))
        semantic["source_snapshot_fingerprint"] = rule.get(
            "source_snapshot_fingerprint"
        )
        semantic["source_version_refs"] = list(rule.get("source_version_refs") or [])
        rule["semantic_contract"] = semantic
    return annotated


def _filter_active_projection_artifacts(
    asset: dict[str, Any], active_rule_ids: set[str]
) -> None:
    asset["relationships"] = [
        dict(row)
        for row in _list(asset.get("relationships"))
        if isinstance(row, dict)
        and not (
            _text(row.get("derivation")) == _DERIVATION
            and _text(row.get("from") or row.get("from_ref")) not in active_rule_ids
        )
    ]
    asset["risk_domains"] = [
        dict(row)
        for row in _list(asset.get("risk_domains"))
        if isinstance(row, dict)
        and not (
            _text(row.get("derivation")) == _DERIVATION
            and _text(row.get("source_rule_id")) not in active_rule_ids
        )
    ]
    asset["oracle_library"] = [
        dict(row)
        for row in _list(asset.get("oracle_library"))
        if isinstance(row, dict)
        and not (
            _text(row.get("derivation")) == _DERIVATION
            and _text(row.get("rule_id")) not in active_rule_ids
        )
    ]


def _stale_receipt_rows(lifecycle: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "rule",
            "rule_id": row.get("rule_id"),
            "candidate_id": row.get("candidate_id"),
            "status": "STALE",
            "reason": row.get("reason"),
            "source_version_refs": list(row.get("source_version_refs") or []),
            "current_source_version_refs": list(
                row.get("current_source_version_refs") or []
            ),
            "execution_allowed": False,
            "lifecycle_event_id": row.get("last_event_id"),
        }
        for row in _list(lifecycle.get("items"))
        if isinstance(row, dict) and _text(row.get("status")).upper() == "STALE"
    ]


def _merge_validation_stale(asset: dict[str, Any], stale_rows: list[dict[str, Any]]) -> None:
    receipt = dict(_dict(asset.get("implicit_rule_candidate_validation_receipt")))
    receipt["stale"] = stale_rows
    receipt["stale_count"] = len(stale_rows)
    per_kind = dict(_dict(receipt.get("per_kind")))
    rule_counts = dict(_dict(per_kind.get("rule")))
    rule_counts["stale"] = len(stale_rows)
    per_kind["rule"] = rule_counts
    receipt["per_kind"] = per_kind
    asset["implicit_rule_candidate_validation_receipt"] = receipt


def _governance_gaps(
    asset: dict[str, Any],
    lifecycle: dict[str, Any],
    decision_ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) not in _GOVERNANCE_GAP_KINDS
    ]
    for row in _list(lifecycle.get("items")):
        if not isinstance(row, dict) or _text(row.get("status")).upper() != "STALE":
            continue
        gaps.append(
            {
                "gap_id": _stable_id("implicit_rule_stale_gap", row.get("rule_id"), row.get("reason")),
                "kind": "IMPLICIT_RULE_STALE",
                "gap_type": "previously_accepted_rule_not_currently_authoritative",
                "rule_id": row.get("rule_id"),
                "candidate_id": row.get("candidate_id"),
                "reason": row.get("reason"),
                "source_version_refs": list(row.get("source_version_refs") or []),
                "current_source_version_refs": list(
                    row.get("current_source_version_refs") or []
                ),
                "execution_allowed": False,
                "operator_action": (
                    "review the changed source and either provide current authority or record an explicit authority decision"
                ),
            }
        )
    for row in _list(decision_ledger.get("items")):
        if not isinstance(row, dict):
            continue
        status = _text(row.get("status")).upper()
        if status == "PENDING_EVIDENCE":
            gaps.append(
                {
                    "gap_id": _stable_id("implicit_rule_decision_pending", row.get("decision_id")),
                    "kind": "IMPLICIT_RULE_AUTHORITY_DECISION_PENDING",
                    "gap_type": "authority_decision_evidence_not_verifiable",
                    "decision_id": row.get("decision_id"),
                    "rule_id": row.get("rule_ref"),
                    "reason_code": row.get("reason_code"),
                    "missing_runtime_evidence_refs": list(
                        row.get("missing_runtime_evidence_refs") or []
                    ),
                    "operator_action": "import the content-addressed runtime evidence receipt",
                }
            )
        elif status == "REJECTED":
            gaps.append(
                {
                    "gap_id": _stable_id("implicit_rule_decision_rejected", row.get("decision_id")),
                    "kind": "IMPLICIT_RULE_AUTHORITY_DECISION_REJECTED",
                    "gap_type": "authority_decision_contract_invalid",
                    "decision_id": row.get("decision_id"),
                    "rule_id": row.get("rule_ref"),
                    "reason_code": row.get("reason_code"),
                    "operator_action": "submit a source-backed decision through the authority contract",
                }
            )
    for row in _list(decision_ledger.get("conflicts")):
        if not isinstance(row, dict):
            continue
        gaps.append(
            {
                "gap_id": _stable_id("implicit_rule_decision_conflict", row),
                "kind": "IMPLICIT_RULE_AUTHORITY_DECISION_CONFLICT",
                "gap_type": "append_only_decision_identity_conflict",
                **row,
                "operator_action": "use a new decision_id; existing decision history is immutable",
            }
        )
    unique: dict[str, dict[str, Any]] = {}
    for row in gaps:
        identity = _text(row.get("gap_id")) or _stable_id("coverage_gap", row)
        unique.setdefault(identity, row)
    return list(unique.values())


def enrich_asset_with_governed_implicit_rule_projection(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Run projection, reconcile one rule identity, then close source/decision lifecycle."""

    before_rules = [
        dict(row)
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict)
    ]
    prior_derived_rules = _derived_rules(before_rules)

    projected = reconcile_implicit_rule_identities(
        enrich_asset_with_implicit_rule_projection(asset)
    )
    projected_rules = [
        dict(row)
        for row in _list(projected.get("rule_library"))
        if isinstance(row, dict)
    ]
    explicit = _explicit_rules(projected_rules)
    accepted = _annotate_promoted_rules(_derived_rules(projected_rules), projected)

    lifecycle = project_implicit_rule_lifecycle(
        projected,
        prior_derived_rules=prior_derived_rules,
        accepted_rules=accepted,
    )
    active_rules, lifecycle, decision_ledger = apply_implicit_rule_authority_decisions(
        projected,
        active_rules=accepted,
        lifecycle=lifecycle,
    )
    active_rule_ids = {
        _text(row.get("rule_id"))
        for row in active_rules
        if _text(row.get("rule_id"))
    }

    projected["rule_library"] = [*explicit, *active_rules]
    projected["implicit_rule_lifecycle_ledger"] = lifecycle
    projected["implicit_rule_authority_decision_ledger"] = decision_ledger
    _filter_active_projection_artifacts(projected, active_rule_ids)

    stale_rows = _stale_receipt_rows(lifecycle)
    _merge_validation_stale(projected, stale_rows)
    projected["coverage_gaps"] = _governance_gaps(
        projected, lifecycle, decision_ledger
    )

    gate = dict(_dict(projected.get("implicit_rule_projection_gate")))
    gate["governance_schema"] = SCHEMA_VERSION
    gate["accepted_rule_count"] = len(active_rules)
    gate["stale_rule_count"] = len(stale_rows)
    gate["authority_decision_applied_count"] = int(
        decision_ledger.get("applied_count") or 0
    )
    gate["authority_decision_pending_count"] = int(
        decision_ledger.get("pending_evidence_count") or 0
    )
    gate["authority_decision_rejected_count"] = int(
        decision_ledger.get("rejected_count") or 0
    )
    gate["stale_rule_execution_allowed"] = False
    gate["raw_runtime_observation_authority_transition_allowed"] = False
    gate["identity_reconciliation_before_lifecycle"] = True
    if _text(gate.get("status")) == "PASS" and stale_rows:
        gate["status"] = "PARTIAL_STALE_AUTHORITY"
    elif (
        _text(gate.get("status")) == "PASS"
        and int(decision_ledger.get("pending_evidence_count") or 0) > 0
    ):
        gate["status"] = "PARTIAL_AUTHORITY_DECISION_EVIDENCE"
    projected["implicit_rule_projection_gate"] = gate

    summary = dict(_dict(projected.get("summary")))
    summary.update(
        {
            "implicit_rule_accepted_count": len(active_rules),
            "implicit_rule_stale_count": len(stale_rows),
            "implicit_rule_authority_decision_applied_count": int(
                decision_ledger.get("applied_count") or 0
            ),
            "implicit_rule_authority_decision_pending_count": int(
                decision_ledger.get("pending_evidence_count") or 0
            ),
        }
    )
    projected["summary"] = summary

    governance = dict(_dict(projected.get("governance")))
    governance.update(
        {
            "implicit_rule_source_version_lifecycle_is_explicit": True,
            "implicit_rule_stale_history_is_preserved": True,
            "implicit_rule_stale_rules_enter_behavior_ir": False,
            "implicit_rule_authority_decisions_are_append_only": True,
            "implicit_rule_counterexample_requires_content_addressed_evidence": True,
            "raw_runtime_observation_can_demote_rule": False,
            "implicit_rule_governance_reuses_existing_projection_authority": True,
            "implicit_rule_identity_reconciliation_precedes_lifecycle": True,
        }
    )
    projected["governance"] = governance
    return projected


__all__ = [
    "SCHEMA_VERSION",
    "enrich_asset_with_governed_implicit_rule_projection",
]
