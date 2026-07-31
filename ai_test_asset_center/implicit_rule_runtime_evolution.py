"""Project governed runtime evidence for accepted implicit rules.

Runtime conformance or violation is evidence about the target, not authority over the
rule. This projector therefore never promotes, demotes or rewrites a rule. It links
attempts through obligation -> Behavior IR invariant -> source rule identity and emits
an immutable evidence receipt. A legitimate counterexample still requires a separate,
authority-backed operator decision.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

SCHEMA_VERSION = "qualibug.implicit-rule-runtime-evolution.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _obligation_rows(payload: Any) -> list[dict[str, Any]]:
    root = _dict(payload)
    rows = root.get("obligations") if "obligations" in root else payload
    return [dict(row) for row in _list(rows) if isinstance(row, dict)]


def _attempt_rows(payload: Any) -> list[dict[str, Any]]:
    root = _dict(payload)
    rows = root.get("attempts") if "attempts" in root else payload
    return [dict(row) for row in _list(rows) if isinstance(row, dict)]


def _implicit_rule_refs_for_obligation(
    obligation: dict[str, Any],
    invariants_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    prop = _dict(obligation.get("property"))
    refs: set[str] = set()
    for field in ("rule_id", "source_rule_ref", "source_rule_refs"):
        values = prop.get(field) if field in prop else obligation.get(field)
        for value in _list(values) if isinstance(values, list) else [values]:
            if _text(value).startswith("implicit_rule_"):
                refs.add(_text(value))

    invariant_refs: set[str] = set()
    for container in (obligation, prop):
        for field in (
            "invariant_ref",
            "invariant_refs",
            "behavior_ir_ref",
            "behavior_ir_refs",
        ):
            values = container.get(field)
            for value in _list(values) if isinstance(values, list) else [values]:
                if _text(value):
                    invariant_refs.add(_text(value))
    for source_ref in _list(obligation.get("source_refs")):
        if isinstance(source_ref, str) and source_ref in invariants_by_id:
            invariant_refs.add(source_ref)
        elif isinstance(source_ref, dict):
            for field in ("id", "ref", "invariant_ref", "behavior_ir_ref"):
                value = _text(source_ref.get(field))
                if value in invariants_by_id:
                    invariant_refs.add(value)

    for invariant_ref in invariant_refs:
        invariant = invariants_by_id.get(invariant_ref)
        if not invariant:
            continue
        for value in _list(invariant.get("source_rule_refs")):
            if _text(value).startswith("implicit_rule_"):
                refs.add(_text(value))
    return sorted(refs)


def _observation_class(attempt: dict[str, Any]) -> str:
    terminal = _text(attempt.get("terminal_status")).upper()
    reason = _text(attempt.get("reason_code")).upper()
    delivery_gate_status = _text(attempt.get("delivery_gate_status")).upper()
    if terminal == "DELIVERABLE" and delivery_gate_status in {
        "DELIVERABLE",
        "PASS",
        "PASSED",
    }:
        return "OBSERVED_VIOLATION"
    if terminal == "REJECTED" and (
        reason in {"ORACLE_NOT_VIOLATED", "ASSERTION_NOT_VIOLATED"}
        or reason.endswith("_NOT_VIOLATED")
    ):
        return "OBSERVED_CONFORMANCE"
    if terminal in {"DELIVERABLE", "REJECTED"}:
        return "OBSERVED_NO_RULE_VERDICT"
    return "NOT_OBSERVED"


def project_implicit_rule_runtime_evolution(
    *,
    behavior_ir: Any,
    obligations: Any,
    obligation_attempt_ledger: Any,
) -> dict[str, Any]:
    """Build one immutable runtime evidence receipt without changing authority."""
    ir = _dict(behavior_ir)
    invariants_by_id = {
        _text(row.get("id")): dict(row)
        for row in _list(ir.get("invariants"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    obligations_by_id = {
        _text(row.get("obligation_id")): row
        for row in _obligation_rows(obligations)
        if _text(row.get("obligation_id"))
    }
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unlinked: list[dict[str, Any]] = []

    for attempt in _attempt_rows(obligation_attempt_ledger):
        obligation_id = _text(attempt.get("obligation_id"))
        obligation = obligations_by_id.get(obligation_id, {})
        rule_refs = _implicit_rule_refs_for_obligation(
            obligation,
            invariants_by_id,
        )
        if not rule_refs:
            unlinked.append(
                {
                    "obligation_id": obligation_id,
                    "attempt_id": attempt.get("attempt_id"),
                    "reason_code": "IMPLICIT_RULE_IDENTITY_NOT_LINKED",
                }
            )
            continue
        evidence = {
            "obligation_id": obligation_id,
            "attempt_id": attempt.get("attempt_id"),
            "observation_class": _observation_class(attempt),
            "terminal_status": attempt.get("terminal_status"),
            "reason_code": attempt.get("reason_code"),
            "finding_ids": list(attempt.get("finding_ids") or []),
            "compile_receipt_ids": list(attempt.get("compile_receipt_ids") or []),
            "execution_receipt_ids": list(attempt.get("execution_receipt_ids") or []),
            "oracle_receipt_ids": list(attempt.get("oracle_receipt_ids") or []),
            "delivery_gate_receipt_ids": list(
                attempt.get("delivery_gate_receipt_ids") or []
            ),
            "authority_transition": "UNCHANGED",
            "authority_reason": "raw_runtime_observation_cannot_rewrite_rule_authority",
        }
        for rule_ref in rule_refs:
            by_rule[rule_ref].append(dict(evidence))

    rule_rows: list[dict[str, Any]] = []
    total_classes: Counter[str] = Counter()
    for rule_ref, evidence_rows in sorted(by_rule.items()):
        classes = Counter(
            _text(row.get("observation_class")) for row in evidence_rows
        )
        total_classes.update(classes)
        rule_rows.append(
            {
                "rule_ref": rule_ref,
                "evidence_count": len(evidence_rows),
                "observation_distribution": dict(sorted(classes.items())),
                "authority_transition": "UNCHANGED",
                "legitimate_counterexample_decision_required": True,
                "evidence": evidence_rows,
            }
        )

    status = "OBSERVED" if rule_rows else "NO_IMPLICIT_RULE_ATTEMPTS"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "behavior_ir_model_id": ir.get("model_id"),
        "source_snapshot_hash": ir.get("source_snapshot_hash"),
        "rule_count": len(rule_rows),
        "linked_attempt_count": sum(row["evidence_count"] for row in rule_rows),
        "unlinked_attempt_count": len(unlinked),
        "observation_distribution": dict(sorted(total_classes.items())),
        "rules": rule_rows,
        "unlinked_attempts": unlinked,
        "authority_mutated": False,
        "runtime_violation_is_target_bug_evidence_not_rule_counterevidence": True,
        "runtime_conformance_alone_can_promote_rule": False,
        "legitimate_counterexample_requires_authority_decision": True,
    }


__all__ = [
    "SCHEMA_VERSION",
    "project_implicit_rule_runtime_evolution",
]
