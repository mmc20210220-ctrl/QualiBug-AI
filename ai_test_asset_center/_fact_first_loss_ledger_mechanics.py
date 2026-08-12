"""Fact-level first-loss ledger (product, ground-truth free).

Joins FactExperimentabilityReceipts to obligations, experiments, attempt ledger
terminals, and formal delivery projections. Never reads hidden ground truth.

Schema: qualibug.fact-first-loss-ledger.v1
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_SCHEMA = "qualibug.fact-first-loss-ledger.v1"
EXPERIMENTABILITY_REPORT_SCHEMA = "qualibug.fact-experimentability-report.v1"

FIRST_LOSS_STAGES = (
    "SOURCE_NOT_INGESTED",
    "FACT_NOT_EXTRACTED",
    "FACT_CONFLICTED",
    "FACT_NOT_SELECTED",
    "HYPOTHESIS_NOT_GENERATED",
    "OBLIGATION_NOT_GENERATED",
    "FACT_LINEAGE_UNRESOLVED",
    "ABSTRACT_EXPERIMENT_NOT_COMPILED",
    "MATERIALIZATION_BLOCKED",
    "FIXTURE_BLOCKED",
    "ACTOR_BLOCKED",
    "PRECONDITION_BLOCKED",
    "OPERATION_BINDING_BLOCKED",
    "OBSERVER_BLOCKED",
    "CLEANUP_BLOCKED",
    "EXECUTION_BLOCKED",
    "EXECUTION_FAILED",
    "EFFECT_NOT_OBSERVED",
    "ORACLE_INDETERMINATE",
    "FINDING_FILTERED",
    "DELIVERY_FILTERED",
    "EVALUATOR_NOT_MATCHED",
    "TRUE_POSITIVE",
    "FALSE_POSITIVE",
    "NO_LOSS_OBSERVED",
)

_EXPERIMENTABILITY_TO_STAGE = {
    "CONFLICTED_FACT": "FACT_CONFLICTED",
    "NOT_TEST_WORTHY": "FACT_NOT_SELECTED",
    "INSUFFICIENT_SOURCE_AUTHORITY": "SOURCE_NOT_INGESTED",
    "MISSING_PRIMARY_OPERATION": "OPERATION_BINDING_BLOCKED",
    "AMBIGUOUS_OPERATION": "OPERATION_BINDING_BLOCKED",
    "MISSING_BINDING": "OPERATION_BINDING_BLOCKED",
    "MISSING_ACTOR": "ACTOR_BLOCKED",
    "MISSING_CREDENTIAL": "ACTOR_BLOCKED",
    "MISSING_PRECONDITION": "PRECONDITION_BLOCKED",
    "MISSING_FIXTURE": "FIXTURE_BLOCKED",
    "MISSING_OBSERVER": "OBSERVER_BLOCKED",
    "MISSING_CLEANUP": "CLEANUP_BLOCKED",
    "NON_REVERSIBLE_WRITE": "CLEANUP_BLOCKED",
    "UNSAFE_OPERATION": "EXECUTION_BLOCKED",
}

_REASON_TO_STAGE = (
    ("MISSING_BINDING", "OPERATION_BINDING_BLOCKED"),
    ("BLOCKED_MISSING_BINDING", "OPERATION_BINDING_BLOCKED"),
    ("MISSING_FIXTURE", "FIXTURE_BLOCKED"),
    ("BLOCKED_MISSING_FIXTURE", "FIXTURE_BLOCKED"),
    ("MISSING_ACTOR", "ACTOR_BLOCKED"),
    ("MISSING_CREDENTIAL", "ACTOR_BLOCKED"),
    ("PRECONDITION", "PRECONDITION_BLOCKED"),
    ("MISSING_OBSERVER", "OBSERVER_BLOCKED"),
    ("BLOCKED_MISSING_OBSERVER", "OBSERVER_BLOCKED"),
    ("CLEANUP", "CLEANUP_BLOCKED"),
    ("NON_REVERSIBLE", "CLEANUP_BLOCKED"),
    ("BLOCKED_NON_REVERSIBLE", "CLEANUP_BLOCKED"),
    ("MATERIALIZ", "MATERIALIZATION_BLOCKED"),
    ("BUDGET", "FACT_NOT_SELECTED"),
    ("NOT_IN_PLAN", "FACT_NOT_SELECTED"),
    ("EXECUTION", "EXECUTION_BLOCKED"),
    ("HARNESS", "EXECUTION_FAILED"),
    ("ORACLE_INDETERMINATE", "ORACLE_INDETERMINATE"),
    ("INDETERMINATE", "ORACLE_INDETERMINATE"),
    ("EFFECT_NOT", "EFFECT_NOT_OBSERVED"),
    ("OBSERV", "EFFECT_NOT_OBSERVED"),
    ("DELIVERY", "DELIVERY_FILTERED"),
    ("REJECTED", "FINDING_FILTERED"),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in _list(values):
        item = _text(value)
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def extract_fact_refs(value: Any) -> list[str]:
    """Extract fact identity refs from source_refs / fact_refs fields."""

    refs: list[str] = []
    if isinstance(value, dict):
        for key in ("fact_refs", "fact_ref", "fact_id"):
            raw = value.get(key)
            if isinstance(raw, list):
                refs.extend(_text(item) for item in raw)
            elif _text(raw):
                refs.append(_text(raw))
        for item in _list(value.get("source_refs")):
            refs.extend(extract_fact_refs(item))
        return _unique([item for item in refs if item.startswith("fact")])
    if isinstance(value, str):
        text = _text(value)
        return [text] if text.startswith("fact") else []
    if isinstance(value, list):
        for item in value:
            refs.extend(extract_fact_refs(item))
        return _unique(refs)
    return []


def _stage_from_experimentability(status: str) -> str | None:
    return _EXPERIMENTABILITY_TO_STAGE.get(_text(status).upper())


def _stage_from_reason(reason_code: str) -> str | None:
    code = _text(reason_code).upper()
    if not code:
        return None
    for token, stage in _REASON_TO_STAGE:
        if token in code:
            return stage
    return None


def _blocker_owner(stage: str, experimentability_status: str) -> str:
    if stage in {
        "SOURCE_NOT_INGESTED",
        "FACT_NOT_EXTRACTED",
        "FACT_CONFLICTED",
        "FACT_NOT_SELECTED",
    }:
        return "enterprise_understanding"
    if stage in {
        "HYPOTHESIS_NOT_GENERATED",
        "OBLIGATION_NOT_GENERATED",
        "ABSTRACT_EXPERIMENT_NOT_COMPILED",
    }:
        return "obligation_compiler"
    if stage == "FACT_LINEAGE_UNRESOLVED":
        return "diagnostic_lineage"
    if stage in {
        "MATERIALIZATION_BLOCKED",
        "FIXTURE_BLOCKED",
        "ACTOR_BLOCKED",
        "PRECONDITION_BLOCKED",
        "OPERATION_BINDING_BLOCKED",
        "OBSERVER_BLOCKED",
        "CLEANUP_BLOCKED",
    }:
        return "runtime_planning"
    if stage in {"EXECUTION_BLOCKED", "EXECUTION_FAILED", "EFFECT_NOT_OBSERVED"}:
        return "experiment_executor"
    if stage in {"ORACLE_INDETERMINATE", "FINDING_FILTERED"}:
        return "contract_oracle"
    if stage in {"DELIVERY_FILTERED"}:
        return "customer_delivery_gate"
    if experimentability_status and experimentability_status != "READY":
        return "fact_experimentability_projection"
    return "discovery_mainline"


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    payload = [
        {
            "fact_ref": row.get("fact_ref"),
            "receipt_id": row.get("receipt_id"),
            "first_loss_stage": row.get("first_loss_stage"),
            "first_loss_reason": row.get("first_loss_reason"),
            "obligation_refs": row.get("obligation_refs"),
            "experiment_refs": row.get("experiment_refs"),
        }
        for row in rows
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _index_obligations_by_fact(
    obligations: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for obligation in obligations:
        if not isinstance(obligation, dict):
            continue
        for fact_ref in extract_fact_refs(obligation):
            index.setdefault(fact_ref, []).append(obligation)
    return index


def _index_experiments_by_obligation(
    experiments: list[dict[str, Any]] | dict[str, Any],
) -> dict[str, dict[str, Any]]:
    by_obligation: dict[str, dict[str, Any]] = {}
    if isinstance(experiments, dict) and isinstance(experiments.get("by_obligation"), dict):
        for oid, row in experiments["by_obligation"].items():
            if isinstance(row, dict) and _text(oid):
                by_obligation[_text(oid)] = row
    rows = experiments if isinstance(experiments, list) else (
        _list(_dict(experiments).get("all_experiments"))
        + _list(_dict(experiments).get("experiments"))
        + _list(_dict(experiments).get("blocked_experiments"))
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        oid = _text(row.get("obligation_id"))
        if oid and oid not in by_obligation:
            by_obligation[oid] = row
    return by_obligation


def _attempts_by_obligation(attempt_ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("obligation_id")): row
        for row in _list(attempt_ledger.get("attempts"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }


def _deliverable_obligation_ids(v12_result: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in (
        "formal_customer_deliverables",
        "customer_deliverables",
        "delivery_occurrences",
    ):
        for row in _list(v12_result.get(key)):
            if not isinstance(row, dict):
                continue
            oid = _text(
                row.get("obligation_id")
                or _dict(row.get("identity")).get("obligation_id")
                or _dict(row.get("delivery")).get("obligation_id")
            )
            if oid:
                ids.add(oid)
    quality = _dict(v12_result.get("discovery_quality") or v12_result.get("quality"))
    for row in _list(quality.get("formal_customer_deliverables")):
        if isinstance(row, dict):
            oid = _text(row.get("obligation_id"))
            if oid:
                ids.add(oid)
    return ids


def _resolve_first_loss(
    *,
    receipt: dict[str, Any],
    linked_obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    attempts_by_obligation: dict[str, dict[str, Any]],
    deliverable_obligation_ids: set[str],
    fact_lineage_unresolved: bool = False,
) -> tuple[str, str, list[str], list[str], list[str], list[str], list[str], str | None]:
    status = _text(receipt.get("status")).upper()
    blockers = _unique(receipt.get("blocker_codes"))
    obligation_refs = _unique(
        [_text(row.get("obligation_id")) for row in linked_obligations]
    )
    experiment_refs: list[str] = []
    execution_refs: list[str] = []
    observation_refs: list[str] = []
    oracle_refs: list[str] = []
    finding_ref: str | None = None

    early = _stage_from_experimentability(status)
    if early and status != "READY":
        return (
            early,
            status or early,
            obligation_refs,
            experiment_refs,
            execution_refs,
            observation_refs,
            oracle_refs,
            finding_ref,
        )

    if not linked_obligations:
        if fact_lineage_unresolved:
            return (
                "FACT_LINEAGE_UNRESOLVED",
                "fact_to_ir_authority_not_resolved",
                obligation_refs,
                experiment_refs,
                execution_refs,
                observation_refs,
                oracle_refs,
                finding_ref,
            )
        return (
            "OBLIGATION_NOT_GENERATED",
            "no_obligation_linked_to_fact_ref",
            obligation_refs,
            experiment_refs,
            execution_refs,
            observation_refs,
            oracle_refs,
            finding_ref,
        )

    # Prefer the furthest-progressed linked obligation for first-loss attribution.
    best_stage = "ABSTRACT_EXPERIMENT_NOT_COMPILED"
    best_reason = "linked_obligation_not_compiled"
    progress_rank = {
        "ABSTRACT_EXPERIMENT_NOT_COMPILED": 0,
        "MATERIALIZATION_BLOCKED": 1,
        "FIXTURE_BLOCKED": 1,
        "ACTOR_BLOCKED": 1,
        "PRECONDITION_BLOCKED": 1,
        "OPERATION_BINDING_BLOCKED": 1,
        "OBSERVER_BLOCKED": 1,
        "CLEANUP_BLOCKED": 1,
        "FACT_NOT_SELECTED": 2,
        "EXECUTION_BLOCKED": 3,
        "EXECUTION_FAILED": 3,
        "EFFECT_NOT_OBSERVED": 4,
        "ORACLE_INDETERMINATE": 5,
        "FINDING_FILTERED": 6,
        "DELIVERY_FILTERED": 7,
        "NO_LOSS_OBSERVED": 8,
    }

    for obligation in linked_obligations:
        oid = _text(obligation.get("obligation_id"))
        experiment = _dict(experiments_by_obligation.get(oid))
        attempt = _dict(attempts_by_obligation.get(oid))
        exp_id = _text(
            experiment.get("experiment_id")
            or attempt.get("experiment_id")
            or obligation.get("experiment_id")
        )
        if exp_id:
            experiment_refs.append(exp_id)
        compile_receipt = _dict(experiment.get("compile_receipt"))
        compile_status = _text(
            compile_receipt.get("status")
            or experiment.get("compile_status")
            or obligation.get("compile_status")
        ).upper()
        compile_reason = _text(
            compile_receipt.get("reason_code")
            or experiment.get("block_reason")
            or obligation.get("block_reason")
        )
        materialization_status = _text(
            _dict(experiment.get("materialization_receipt")).get("status")
        ).upper()
        selection_status = _text(attempt.get("selection_status")).upper()
        terminal_status = _text(attempt.get("terminal_status")).upper()
        reason_code = _text(attempt.get("reason_code") or compile_reason)
        execution_id = _text(attempt.get("execution_id"))
        if execution_id:
            execution_refs.append(execution_id)
        observation_refs.extend(_list(attempt.get("observation_receipt_ids")))
        oracle_id = _text(attempt.get("oracle_receipt_id"))
        if oracle_id:
            oracle_refs.append(oracle_id)
        finding_id = _text(attempt.get("finding_id"))
        if finding_id:
            finding_ref = finding_id

        stage = "ABSTRACT_EXPERIMENT_NOT_COMPILED"
        reason = compile_reason or "experiment_not_compiled"

        if compile_status == "ABSTRACT" or materialization_status == "NOT_MATERIALIZED":
            stage = _stage_from_reason(compile_reason) or "MATERIALIZATION_BLOCKED"
            reason = compile_reason or materialization_status or stage
        elif compile_status == "BLOCKED" or _text(obligation.get("compile_status")).upper() == "BLOCKED":
            stage = _stage_from_reason(compile_reason) or "MATERIALIZATION_BLOCKED"
            reason = compile_reason or stage
        elif not experiment and not attempt:
            stage = "ABSTRACT_EXPERIMENT_NOT_COMPILED"
            reason = "no_experiment_for_obligation"
        elif selection_status in {"DEFERRED_NOT_SELECTED", "COMPILE_BLOCKED", "PLAN_BLOCKED"}:
            stage = _stage_from_reason(reason_code) or "FACT_NOT_SELECTED"
            reason = reason_code or selection_status
        elif terminal_status == "DELIVERABLE" or oid in deliverable_obligation_ids:
            stage = "NO_LOSS_OBSERVED"
            reason = "formal_customer_deliverable"
            if not finding_ref:
                finding_ref = _text(attempt.get("finding_id")) or None
        elif terminal_status == "REJECTED":
            stage = _stage_from_reason(reason_code) or "FINDING_FILTERED"
            reason = reason_code or "REJECTED"
        elif terminal_status == "BLOCKED":
            stage = _stage_from_reason(reason_code) or "EXECUTION_BLOCKED"
            reason = reason_code or "BLOCKED"
        elif terminal_status == "DEFERRED":
            stage = "FACT_NOT_SELECTED"
            reason = reason_code or "DEFERRED"
        elif terminal_status == "HARNESS_FAILED":
            stage = "EXECUTION_FAILED"
            reason = reason_code or "HARNESS_FAILED"
        elif compile_status == "COMPILED" and not attempt:
            stage = "FACT_NOT_SELECTED"
            reason = "compiled_but_no_attempt_receipt"
        elif compile_status == "COMPILED":
            stage = _stage_from_reason(reason_code) or "EXECUTION_BLOCKED"
            reason = reason_code or terminal_status or "compiled_without_delivery"

        if progress_rank.get(stage, -1) >= progress_rank.get(best_stage, -1):
            # Track furthest progress; for losses keep earliest loss among incomplete paths.
            if stage == "NO_LOSS_OBSERVED" or progress_rank.get(stage, -1) > progress_rank.get(best_stage, -1):
                best_stage = stage
                best_reason = reason

    # If any linked path delivered, fact is not lost on product path.
    if any(
        _text(attempts_by_obligation.get(_text(o.get("obligation_id")), {}).get("terminal_status")).upper()
        == "DELIVERABLE"
        or _text(o.get("obligation_id")) in deliverable_obligation_ids
        for o in linked_obligations
    ):
        best_stage = "NO_LOSS_OBSERVED"
        best_reason = "formal_customer_deliverable"

    # Among non-delivered paths, prefer earliest loss (lowest progress).
    if best_stage != "NO_LOSS_OBSERVED":
        earliest = best_stage
        earliest_reason = best_reason
        earliest_rank = progress_rank.get(best_stage, 99)
        for obligation in linked_obligations:
            oid = _text(obligation.get("obligation_id"))
            attempt = _dict(attempts_by_obligation.get(oid))
            experiment = _dict(experiments_by_obligation.get(oid))
            compile_receipt = _dict(experiment.get("compile_receipt"))
            compile_reason = _text(
                compile_receipt.get("reason_code")
                or experiment.get("block_reason")
                or obligation.get("block_reason")
                or attempt.get("reason_code")
            )
            compile_status = _text(compile_receipt.get("status") or obligation.get("compile_status")).upper()
            terminal_status = _text(attempt.get("terminal_status")).upper()
            if terminal_status == "DELIVERABLE" or oid in deliverable_obligation_ids:
                continue
            if compile_status == "BLOCKED":
                stage = _stage_from_reason(compile_reason) or "MATERIALIZATION_BLOCKED"
            elif not experiment:
                stage = "ABSTRACT_EXPERIMENT_NOT_COMPILED"
            else:
                stage = _stage_from_reason(
                    _text(attempt.get("reason_code") or compile_reason)
                ) or (
                    "EXECUTION_BLOCKED" if terminal_status else "FACT_NOT_SELECTED"
                )
            rank = progress_rank.get(stage, 99)
            if rank < earliest_rank:
                earliest = stage
                earliest_reason = compile_reason or stage
                earliest_rank = rank
        best_stage = earliest
        best_reason = earliest_reason

    return (
        best_stage,
        best_reason or best_stage,
        _unique(obligation_refs),
        _unique(experiment_refs),
        _unique(execution_refs),
        _unique(observation_refs),
        _unique(oracle_refs),
        finding_ref,
    )


def build_fact_first_loss_ledger(
    *,
    fact_experimentability_ledger: dict[str, Any] | None,
    obligations: list[dict[str, Any]] | None = None,
    experiments: list[dict[str, Any]] | dict[str, Any] | None = None,
    obligation_attempt_ledger: dict[str, Any] | None = None,
    fact_lineage_receipt: dict[str, Any] | None = None,
    v12_result: dict[str, Any] | None = None,
    campaign_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Build a GT-free fact first-loss ledger with conservation checks."""

    exp_ledger = _dict(fact_experimentability_ledger)
    receipts = [row for row in _list(exp_ledger.get("items")) if isinstance(row, dict)]
    obl_rows = [row for row in _list(obligations) if isinstance(row, dict)]
    result = _dict(v12_result)
    if not obl_rows:
        plan_obl = _dict(result.get("obligations"))
        obl_rows = [row for row in _list(plan_obl.get("obligations")) if isinstance(row, dict)]
    exp_pack = experiments if experiments is not None else result.get("experiments")
    attempt_ledger = _dict(
        obligation_attempt_ledger or result.get("obligation_attempt_ledger")
    )
    if not campaign_id:
        campaign_id = _text(
            attempt_ledger.get("campaign_id")
            or _dict(attempt_ledger.get("identity")).get("campaign_id")
            or result.get("campaign_id")
        )
    if not run_id:
        run_id = _text(
            attempt_ledger.get("run_id")
            or _dict(attempt_ledger.get("identity")).get("run_id")
            or result.get("run_id")
        )

    obligations_by_fact = _index_obligations_by_fact(obl_rows)
    experiments_by_obligation = _index_experiments_by_obligation(exp_pack or {})
    attempts_by_obligation = _attempts_by_obligation(attempt_ledger)
    deliverable_ids = _deliverable_obligation_ids(result)
    lineage_receipt = _dict(fact_lineage_receipt)
    unresolved_lineage_fact_refs = set(
        _unique(lineage_receipt.get("unresolved_fact_refs"))
    )

    rows: list[dict[str, Any]] = []
    for receipt in sorted(receipts, key=lambda row: _text(row.get("fact_ref"))):
        fact_ref = _text(receipt.get("fact_ref"))
        linked = obligations_by_fact.get(fact_ref, [])
        (
            stage,
            reason,
            obligation_refs,
            experiment_refs,
            execution_refs,
            observation_refs,
            oracle_refs,
            finding_ref,
        ) = _resolve_first_loss(
            receipt=receipt,
            linked_obligations=linked,
            experiments_by_obligation=experiments_by_obligation,
            attempts_by_obligation=attempts_by_obligation,
            deliverable_obligation_ids=deliverable_ids,
            fact_lineage_unresolved=fact_ref in unresolved_lineage_fact_refs,
        )
        if stage not in FIRST_LOSS_STAGES:
            stage = "HYPOTHESIS_NOT_GENERATED"
            reason = f"unknown_stage_coerced:{stage}"
        rows.append(
            {
                "fact_ref": fact_ref,
                "receipt_id": _text(receipt.get("receipt_id")),
                "experimentability_status": _text(receipt.get("status")),
                "risk_operator": _text(receipt.get("risk_operator")),
                "risk_level": _text(receipt.get("risk_level")),
                "hypothesis_ref": "",
                "obligation_refs": obligation_refs,
                "experiment_refs": experiment_refs,
                "execution_refs": execution_refs,
                "observation_refs": observation_refs,
                "oracle_refs": oracle_refs,
                "finding_ref": finding_ref or "",
                "ground_truth_ref": "",
                "first_loss_stage": stage,
                "first_loss_reason": reason,
                "blocker_owner": _blocker_owner(stage, _text(receipt.get("status"))),
                "blocker_codes": _unique(receipt.get("blocker_codes")),
            }
        )

    stage_counts = dict(Counter(_text(row.get("first_loss_stage")) for row in rows))
    conservation_issues: list[str] = []
    if int(exp_ledger.get("receipt_count") or len(receipts)) != len(rows):
        conservation_issues.append(
            "receipt_to_first_loss_count_mismatch:"
            f"receipts={len(receipts)};first_loss_rows={len(rows)}"
        )
    if int(exp_ledger.get("silent_drop_count") or 0) != 0:
        conservation_issues.append(
            f"experimentability_silent_drop:{exp_ledger.get('silent_drop_count')}"
        )
    if sum(stage_counts.values()) != len(rows):
        conservation_issues.append("first_loss_stage_count_not_conserved")

    ledger = {
        "schema_version": LEDGER_SCHEMA,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "fact_experimentability_ledger_fingerprint": _text(
            exp_ledger.get("ledger_fingerprint")
        ),
        "obligation_attempt_ledger_fingerprint": _text(
            attempt_ledger.get("ledger_fingerprint")
        ),
        "fact_lineage_receipt_schema": _text(
            lineage_receipt.get("schema_version")
        ),
        "fact_lineage_authority_status": _text(
            lineage_receipt.get("authority_status")
        ) or "NOT_MEASURED",
        "receipt_count": len(receipts),
        "row_count": len(rows),
        "stage_counts": stage_counts,
        "conservation": {
            "status": "PASS" if not conservation_issues else "FAILED",
            "issues": conservation_issues,
            "accepted_fact_receipt_coverage": (
                len(receipts) == int(exp_ledger.get("accepted_fact_count") or len(receipts))
            ),
            "receipt_to_row_conserved": len(receipts) == len(rows),
            "silent_drop_count": int(exp_ledger.get("silent_drop_count") or 0),
        },
        "ground_truth_joined": False,
        "items": rows,
        "ledger_fingerprint": _fingerprint(rows),
        "created_at": _now_iso(),
    }
    return ledger


def build_fact_experimentability_report(
    fact_experimentability_ledger: dict[str, Any] | None,
    *,
    first_loss_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ledger = _dict(fact_experimentability_ledger)
    first_loss = _dict(first_loss_ledger)
    return {
        "schema_version": EXPERIMENTABILITY_REPORT_SCHEMA,
        "ledger_schema": _text(ledger.get("schema_version")),
        "accepted_fact_count": int(ledger.get("accepted_fact_count") or 0),
        "receipt_count": int(ledger.get("receipt_count") or 0),
        "high_risk_fact_count": int(ledger.get("high_risk_fact_count") or 0),
        "ready_count": int(ledger.get("ready_count") or 0),
        "blocked_count": int(ledger.get("blocked_count") or 0),
        "not_test_worthy_count": int(ledger.get("not_test_worthy_count") or 0),
        "silent_drop_count": int(ledger.get("silent_drop_count") or 0),
        "status_counts": dict(_dict(ledger.get("status_counts"))),
        "ledger_fingerprint": _text(ledger.get("ledger_fingerprint")),
        "first_loss_stage_counts": dict(_dict(first_loss.get("stage_counts"))),
        "first_loss_conservation": dict(_dict(first_loss.get("conservation"))),
        "items": [
            {
                "receipt_id": row.get("receipt_id"),
                "fact_ref": row.get("fact_ref"),
                "status": row.get("status"),
                "risk_operator": row.get("risk_operator"),
                "risk_level": row.get("risk_level"),
                "blocker_codes": row.get("blocker_codes"),
                "required_operation_refs": row.get("required_operation_refs"),
                "observer_refs": row.get("observer_refs"),
            }
            for row in _list(ledger.get("items"))
            if isinstance(row, dict)
        ],
        "created_at": _now_iso(),
    }


def render_fact_experimentability_summary(report: dict[str, Any]) -> str:
    lines = [
        "# Fact Experimentability Summary",
        "",
        f"- accepted_fact_count: {report.get('accepted_fact_count')}",
        f"- receipt_count: {report.get('receipt_count')}",
        f"- high_risk_fact_count: {report.get('high_risk_fact_count')}",
        f"- ready_count: {report.get('ready_count')}",
        f"- blocked_count: {report.get('blocked_count')}",
        f"- not_test_worthy_count: {report.get('not_test_worthy_count')}",
        f"- silent_drop_count: {report.get('silent_drop_count')}",
        f"- ledger_fingerprint: {report.get('ledger_fingerprint')}",
        "",
        "## Status counts",
    ]
    for key, value in sorted(dict(_dict(report.get("status_counts"))).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## First-loss stage counts"])
    stage_counts = dict(_dict(report.get("first_loss_stage_counts")))
    if not stage_counts:
        lines.append("- NOT_MEASURED")
    else:
        for key, value in sorted(stage_counts.items()):
            lines.append(f"- {key}: {value}")
    conservation = _dict(report.get("first_loss_conservation"))
    lines.extend(
        [
            "",
            "## Conservation",
            f"- status: {conservation.get('status') or 'NOT_MEASURED'}",
        ]
    )
    for issue in _list(conservation.get("issues")):
        lines.append(f"- issue: {issue}")
    lines.append("")
    return "\n".join(lines)


def render_first_loss_summary(ledger: dict[str, Any]) -> str:
    lines = [
        "# Fact First-loss Summary",
        "",
        f"- schema_version: {ledger.get('schema_version')}",
        f"- campaign_id: {ledger.get('campaign_id') or 'NOT_BOUND'}",
        f"- run_id: {ledger.get('run_id') or 'NOT_BOUND'}",
        f"- receipt_count: {ledger.get('receipt_count')}",
        f"- row_count: {ledger.get('row_count')}",
        f"- ground_truth_joined: {ledger.get('ground_truth_joined')}",
        f"- ledger_fingerprint: {ledger.get('ledger_fingerprint')}",
        "",
        "## Stage counts",
    ]
    for key, value in sorted(dict(_dict(ledger.get("stage_counts"))).items()):
        lines.append(f"- {key}: {value}")
    conservation = _dict(ledger.get("conservation"))
    lines.extend(
        [
            "",
            "## Conservation",
            f"- status: {conservation.get('status')}",
            f"- receipt_to_row_conserved: {conservation.get('receipt_to_row_conserved')}",
            f"- silent_drop_count: {conservation.get('silent_drop_count')}",
        ]
    )
    for issue in _list(conservation.get("issues")):
        lines.append(f"- issue: {issue}")
    lines.extend(["", "## Rows"])
    for row in _list(ledger.get("items")):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- {row.get('fact_ref')}: {row.get('first_loss_stage')} "
            f"({row.get('first_loss_reason')}) owner={row.get('blocker_owner')}"
        )
    lines.append("")
    return "\n".join(lines)


def _canonical_fact_authority_by_ir_ref(
    *,
    behavior_ir: dict[str, Any],
    knowledge_asset: dict[str, Any],
    accepted_fact_refs: set[str],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]], Counter[str], set[str]]:
    """Resolve exact accepted-fact lineage onto IR invariant/relation IDs.

    The join is deliberately structural: canonical Business World Model
    evidence -> canonical behavior -> governed implementation binding ->
    Behavior IR invariant/relation.  Text, operation names, paths and source
    locators are never matching authorities.
    """

    reasons: Counter[str] = Counter()
    ambiguous_ir_refs: set[str] = set()
    world = _dict(knowledge_asset.get("business_world_model"))
    model = _dict(knowledge_asset.get("enterprise_understanding_model"))

    evidence_fact_refs: dict[str, set[str]] = {}
    for row in _list(world.get("evidence_registry")):
        if not isinstance(row, dict):
            continue
        evidence_ref = _text(row.get("evidence_ref"))
        fact_ref = _text(row.get("fact_id") or row.get("fact_ref"))
        if evidence_ref and fact_ref in accepted_fact_refs:
            evidence_fact_refs.setdefault(evidence_ref, set()).add(fact_ref)
    for fact_refs in evidence_fact_refs.values():
        if len(fact_refs) > 1:
            reasons["AMBIGUOUS_WORLD_MODEL_EVIDENCE_ID"] += 1

    behaviors_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in _list(model.get("business_behaviors")):
        if isinstance(row, dict) and _text(row.get("behavior_id")):
            behaviors_by_id.setdefault(_text(row.get("behavior_id")), []).append(row)

    top_binding_behaviors: dict[str, set[str]] = {}
    api_binding_behaviors: dict[str, set[str]] = {}
    for row in _list(model.get("behavior_implementation_bindings")):
        if not isinstance(row, dict):
            continue
        behavior_ref = _text(row.get("behavior_ref"))
        binding_ref = _text(row.get("binding_id"))
        if behavior_ref and binding_ref:
            top_binding_behaviors.setdefault(binding_ref, set()).add(behavior_ref)
        for api_binding in _list(row.get("api_operation_bindings")):
            if not isinstance(api_binding, dict):
                continue
            api_binding_ref = _text(api_binding.get("binding_id"))
            if (
                behavior_ref
                and api_binding_ref
                and _text(api_binding.get("status")).upper() == "BOUND"
                and api_binding.get("authoritative") is True
            ):
                api_binding_behaviors.setdefault(api_binding_ref, set()).add(
                    behavior_ref
                )
    ambiguous_binding_count = sum(
        1 for values in [*top_binding_behaviors.values(), *api_binding_behaviors.values()]
        if len(values) > 1
    )
    if ambiguous_binding_count:
        reasons["AMBIGUOUS_IMPLEMENTATION_BINDING_ID"] = ambiguous_binding_count

    world_nodes_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in _list(world.get("behavior_nodes")):
        if isinstance(row, dict) and _text(row.get("node_id")):
            world_nodes_by_id.setdefault(_text(row.get("node_id")), []).append(row)

    behavior_fact_refs: dict[str, tuple[str, ...]] = {}
    for behavior_ref, nodes in world_nodes_by_id.items():
        if len(nodes) != 1 or len(behaviors_by_id.get(behavior_ref, [])) != 1:
            reasons["AMBIGUOUS_BEHAVIOR_ID"] += 1
            continue
        node = nodes[0]
        behavior = behaviors_by_id[behavior_ref][0]
        node_binding_refs = _unique(node.get("implementation_binding_refs"))
        if not node_binding_refs or any(
            top_binding_behaviors.get(binding_ref) != {behavior_ref}
            for binding_ref in node_binding_refs
        ):
            reasons["BEHAVIOR_IMPLEMENTATION_BINDING_UNRESOLVED"] += 1
            continue
        world_fact_refs: set[str] = set()
        evidence_ambiguous = False
        for evidence_ref in _unique(node.get("evidence_refs")):
            resolved = evidence_fact_refs.get(evidence_ref, set())
            if len(resolved) > 1:
                evidence_ambiguous = True
                break
            world_fact_refs.update(resolved)
        if evidence_ambiguous:
            reasons["BEHAVIOR_EVIDENCE_AUTHORITY_AMBIGUOUS"] += 1
            continue
        behavior_source_fact_refs = set(extract_fact_refs(behavior)) & accepted_fact_refs
        fact_refs = world_fact_refs & behavior_source_fact_refs
        if not fact_refs:
            reasons["BEHAVIOR_FACT_AUTHORITY_UNRESOLVED"] += 1
            continue
        behavior_fact_refs[behavior_ref] = tuple(sorted(fact_refs))

    invariants_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in _list(behavior_ir.get("invariants")):
        if isinstance(row, dict) and _text(row.get("id")):
            invariants_by_id.setdefault(_text(row.get("id")), []).append(row)

    # Production channel: an invariant built from a fact-promoted rule carries
    # the canonical fact identity on the node itself (behavior_ir_core attaches
    # rule.semantic_contract.fact_id at IR construction). The rule→fact
    # construction link is exact identity — the rule literally IS the fact —
    # so it resolves even when the world-model binding chain is absent, and it
    # must agree with any world-model resolution (conflict fails closed).
    production_invariant_fact_refs: dict[str, tuple[str, ...]] = {}
    for invariant_ref, rows in invariants_by_id.items():
        if len(rows) != 1:
            continue
        refs = tuple(sorted({
            _text(value)
            for value in _list(rows[0].get("fact_refs"))
            if _text(value) and _text(value) in accepted_fact_refs
        }))
        if refs:
            production_invariant_fact_refs[invariant_ref] = refs

    invariant_fact_refs: dict[str, tuple[str, ...]] = {}
    invariant_authority_refs: dict[str, set[str]] = {}
    for invariant_ref, rows in invariants_by_id.items():
        if len(rows) != 1:
            reasons["AMBIGUOUS_INVARIANT_ID"] += 1
            ambiguous_ir_refs.add(invariant_ref)
            continue
        invariant = rows[0]
        behavior_ref = _text(invariant.get("business_behavior_ref"))
        fact_refs = behavior_fact_refs.get(behavior_ref)
        api_binding_refs = _unique(invariant.get("implementation_binding_refs"))
        production_refs = production_invariant_fact_refs.get(invariant_ref)
        if production_refs:
            if fact_refs and set(fact_refs) != set(production_refs):
                reasons["INVARIANT_FACT_AUTHORITY_CHANNEL_CONFLICT"] += 1
                ambiguous_ir_refs.add(invariant_ref)
                continue
            invariant_fact_refs[invariant_ref] = production_refs
            invariant_authority_refs[invariant_ref] = {
                behavior_ref,
                *api_binding_refs,
            } if behavior_ref or api_binding_refs else {f"invariant:{invariant_ref}"}
            continue
        if not behavior_ref or not fact_refs or not api_binding_refs:
            continue
        if any(
            api_binding_behaviors.get(binding_ref) != {behavior_ref}
            for binding_ref in api_binding_refs
        ):
            reasons["INVARIANT_IMPLEMENTATION_BINDING_UNRESOLVED"] += 1
            ambiguous_ir_refs.add(invariant_ref)
            continue
        invariant_fact_refs[invariant_ref] = fact_refs
        invariant_authority_refs[invariant_ref] = {
            behavior_ref,
            *api_binding_refs,
        }

    relations_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in _list(behavior_ir.get("relations")):
        if isinstance(row, dict) and _text(row.get("id")):
            relations_by_id.setdefault(_text(row.get("id")), []).append(row)

    relation_fact_refs: dict[str, tuple[str, ...]] = {}
    for relation_ref, rows in relations_by_id.items():
        if len(rows) != 1:
            reasons["AMBIGUOUS_RELATION_ID"] += 1
            ambiguous_ir_refs.add(relation_ref)
            continue
        relation = rows[0]
        incident_invariants = {
            ref
            for ref in (_text(relation.get("from_ref")), _text(relation.get("to_ref")))
            if ref in invariant_fact_refs
        }
        if len(incident_invariants) > 1:
            reasons["RELATION_INVARIANT_AUTHORITY_AMBIGUOUS"] += 1
            ambiguous_ir_refs.add(relation_ref)
            continue
        if len(incident_invariants) != 1:
            continue
        invariant_ref = next(iter(incident_invariants))
        source_relationship_ref = _text(relation.get("source_relationship_ref"))
        if source_relationship_ref not in invariant_authority_refs[invariant_ref]:
            reasons["RELATION_IMPLEMENTATION_BINDING_UNRESOLVED"] += 1
            continue
        relation_fact_refs[relation_ref] = invariant_fact_refs[invariant_ref]

    return invariant_fact_refs, relation_fact_refs, reasons, ambiguous_ir_refs


def attach_fact_refs_to_planning_artifacts(
    *,
    obligations: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
    fact_experimentability_ledger: dict[str, Any] | None = None,
    behavior_ir: dict[str, Any] | None = None,
    knowledge_asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stamp authority-projected fact refs onto obligations/experiments.

    Does not change compile status, selection, or execution decisions.
    """

    ledger = _dict(fact_experimentability_ledger)
    accepted_fact_refs = {
        _text(row.get("fact_ref"))
        for row in _list(ledger.get("items"))
        if isinstance(row, dict) and _text(row.get("fact_ref"))
    }
    authority_requested = bool(behavior_ir) or bool(knowledge_asset)
    reason_counts: Counter[str] = Counter()
    invariant_fact_refs: dict[str, tuple[str, ...]] = {}
    relation_fact_refs: dict[str, tuple[str, ...]] = {}
    ambiguous_ir_refs: set[str] = set()
    if authority_requested:
        if not behavior_ir or not knowledge_asset or not accepted_fact_refs:
            reason_counts["FACT_LINEAGE_AUTHORITY_INPUT_MISSING"] += 1
        else:
            (
                invariant_fact_refs,
                relation_fact_refs,
                authority_reasons,
                ambiguous_ir_refs,
            ) = _canonical_fact_authority_by_ir_ref(
                behavior_ir=_dict(behavior_ir),
                knowledge_asset=_dict(knowledge_asset),
                accepted_fact_refs=accepted_fact_refs,
            )
            reason_counts.update(authority_reasons)

    stamped_obligations = 0
    stamped_experiments = 0
    linked_fact_refs: set[str] = set()
    for obligation in obligations:
        if not isinstance(obligation, dict):
            continue
        refs = extract_fact_refs(obligation)
        if accepted_fact_refs:
            refs = [ref for ref in refs if ref in accepted_fact_refs]
        if authority_requested and not refs:
            prop = _dict(obligation.get("property"))
            structural_refs = {
                _text(prop.get("invariant_ref")),
                *(_unique(obligation.get("relation_refs"))),
                *(_unique(obligation.get("subject_refs"))),
            }
            structural_refs.discard("")
            if structural_refs & ambiguous_ir_refs:
                reason_counts["OBLIGATION_FACT_AUTHORITY_AMBIGUOUS"] += 1
                structural_sets: list[tuple[str, ...]] = []
            else:
                structural_sets = [
                    values
                    for ref in sorted(structural_refs)
                    for values in (
                        invariant_fact_refs.get(ref),
                        relation_fact_refs.get(ref),
                    )
                    if values
                ]
            distinct_sets = {values for values in structural_sets}
            if len(distinct_sets) == 1:
                refs = list(next(iter(distinct_sets)))
            elif len(distinct_sets) > 1:
                reason_counts["OBLIGATION_FACT_AUTHORITY_AMBIGUOUS"] += 1
            else:
                reason_counts["OBLIGATION_FACT_AUTHORITY_UNRESOLVED"] += 1
        if refs:
            obligation["fact_refs"] = refs
            linked_fact_refs.update(refs)
            stamped_obligations += 1
    experiments_by_obligation: dict[str, list[dict[str, Any]]] = {}
    for row in experiments:
        if not isinstance(row, dict) or not _text(row.get("obligation_id")):
            continue
        experiments_by_obligation.setdefault(
            _text(row.get("obligation_id")), []
        ).append(row)
    for obligation in obligations:
        if not isinstance(obligation, dict):
            continue
        oid = _text(obligation.get("obligation_id"))
        for experiment in experiments_by_obligation.get(oid, []):
            refs = _unique(
                [
                    *extract_fact_refs(obligation),
                    *extract_fact_refs(experiment),
                ]
            )
            if refs:
                experiment["fact_refs"] = refs
                stamped_experiments += 1
    unresolved_count = int(
        reason_counts.get("OBLIGATION_FACT_AUTHORITY_UNRESOLVED", 0)
        + reason_counts.get("OBLIGATION_FACT_AUTHORITY_AMBIGUOUS", 0)
    )
    authority_resolved_fact_refs = {
        fact_ref
        for refs in invariant_fact_refs.values()
        for fact_ref in refs
    }
    return {
        "schema_version": "qualibug.fact-ref-planning-attach.v1",
        "authority_status": (
            "NOT_REQUESTED"
            if not authority_requested
            else "BLOCKED_WITH_GAPS"
            if reason_counts
            else "PASS"
        ),
        "authority": (
            "canonical_business_world_model_to_behavior_ir_exact_identity"
        ),
        "heuristic_matching_enabled": False,
        "stamped_obligation_count": stamped_obligations,
        "stamped_experiment_count": stamped_experiments,
        "unresolved_obligation_count": unresolved_count,
        "authority_resolved_fact_refs": sorted(authority_resolved_fact_refs),
        "linked_fact_refs": sorted(linked_fact_refs),
        "unresolved_fact_refs": sorted(
            accepted_fact_refs - authority_resolved_fact_refs
        ) if authority_requested else [],
        "reason_counts": dict(sorted(reason_counts.items())),
        "fact_experimentability_ledger_fingerprint": _text(
            ledger.get("ledger_fingerprint")
        ),
        "changes_compile_or_execution_decisions": False,
    }


def build_fact_first_loss_from_v12_result(v12_result: dict[str, Any]) -> dict[str, Any]:
    result = _dict(v12_result)
    asset = _dict(
        result.get("knowledge_asset")
        or _dict(result.get("experiments")).get("_knowledge_asset")
    )
    exp_ledger = _dict(
        result.get("fact_experimentability_ledger")
        or _dict(result.get("experiments")).get("fact_experimentability_ledger")
        or _dict(result.get("v12")).get("fact_experimentability_ledger")
        or asset.get("fact_experimentability_ledger")
        or _dict(result.get("enterprise_understanding_model")).get(
            "fact_experimentability_ledger"
        )
    )
    obligation_pack = _dict(result.get("obligations"))
    obligations = _list(obligation_pack.get("obligations"))
    if not obligations:
        obligations = _list(result.get("obligations"))
    return build_fact_first_loss_ledger(
        fact_experimentability_ledger=exp_ledger,
        obligations=obligations,
        experiments=result.get("experiments"),
        obligation_attempt_ledger=result.get("obligation_attempt_ledger"),
        fact_lineage_receipt=(
            _dict(obligation_pack.get("fact_ref_attach_receipt"))
            or _dict(_dict(result.get("experiments")).get("fact_ref_attach_receipt"))
        ),
        v12_result=result,
    )


def write_fact_tracking_report_files(
    v12_result: dict[str, Any],
    output_dir: Path | str,
) -> dict[str, str]:
    """Persist redacted experimentability + first-loss reports."""

    from .artifact_redactor import redact_and_validate, write_json_redacted

    result = _dict(v12_result)
    first_loss = build_fact_first_loss_from_v12_result(result)
    asset = _dict(
        result.get("knowledge_asset")
        or _dict(result.get("experiments")).get("_knowledge_asset")
    )
    exp_ledger = _dict(
        result.get("fact_experimentability_ledger")
        or _dict(result.get("v12")).get("fact_experimentability_ledger")
        or asset.get("fact_experimentability_ledger")
    )
    report = build_fact_experimentability_report(
        exp_ledger,
        first_loss_ledger=first_loss,
    )
    result["fact_experimentability_report"] = report
    result["fact_first_loss_ledger"] = first_loss

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "fact_experimentability_report": str(target / "fact_experimentability_report.json"),
        "fact_experimentability_summary": str(target / "fact_experimentability_summary.md"),
        "first_loss_ledger": str(target / "first_loss_ledger.json"),
        "first_loss_summary": str(target / "first_loss_summary.md"),
    }
    write_json_redacted(paths["fact_experimentability_report"], report)
    write_json_redacted(paths["first_loss_ledger"], first_loss)
    redacted_report, _ = redact_and_validate(report)
    redacted_ledger, _ = redact_and_validate(first_loss)
    Path(paths["fact_experimentability_summary"]).write_text(
        render_fact_experimentability_summary(redacted_report),
        encoding="utf-8",
    )
    Path(paths["first_loss_summary"]).write_text(
        render_first_loss_summary(redacted_ledger),
        encoding="utf-8",
    )
    return paths


__all__ = [
    "LEDGER_SCHEMA",
    "EXPERIMENTABILITY_REPORT_SCHEMA",
    "FIRST_LOSS_STAGES",
    "extract_fact_refs",
    "attach_fact_refs_to_planning_artifacts",
    "build_fact_first_loss_ledger",
    "build_fact_experimentability_report",
    "build_fact_first_loss_from_v12_result",
    "render_fact_experimentability_summary",
    "render_first_loss_summary",
    "write_fact_tracking_report_files",
]
